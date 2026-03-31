# Architecture

## Overview

Zane is a multi-container application running on a single Docker network (`app-network`). The system follows a task-queue architecture: the frontend submits user messages to the backend, which enqueues agent tasks in Redis. An independent agent worker picks tasks from the queue, orchestrates AI processing through MCP tool servers, and publishes results back through Redis pub/sub. The backend subscribes to these updates and pushes them to the frontend via WebSocket.

This architecture means the backend never blocks waiting for the agent. The agent can crash and restart without losing tasks (Redis persists the queue). Agent runs can take up to 30 minutes -- the user can close the browser and return later without losing any data.

## Container map

| # | Container | Technology | Port | Role |
|---|-----------|------------|------|------|
| 1 | `frontend` | React / Next.js | 3000 | Chat UI with file upload, BOM display, download buttons |
| 2 | `backend` | Python / FastAPI | 8000 | REST API, file handling, Supabase CRUD, WebSocket relay |
| 3 | `agent` | Anthropic Agent SDK | internal | Worker process -- picks tasks from Redis, calls MCP tools |
| 4 | `litellm-proxy` | LiteLLM Proxy Server | 4000 | Translates Anthropic SDK format to OpenAI API format |
| 5 | `redis` | Redis 7 | 6379 | Task queue + pub/sub for agent status updates |
| 6 | `minio` | MinIO | 9000 (API), 9001 (console) | Object storage for uploads, temp files, exports |
| 7 | `mcp-nexar` | Python MCP server | 8001 | Nexar/Octopart component search (GraphQL, OAuth2) |
| 8 | `mcp-snapmagic` | Python MCP server | 8002 | CAD model availability check (SnapMagic/SnapEDA) |
| 9 | `mcp-documents` | Python MCP server | 8003 | PDF rendering, image processing, base64 conversion |
| 10 | `mcp-websearch` | Python MCP server | 8004 | Web search fallback for distributor lookups |
| 11 | `mcp-export` | Python MCP server | 8005 | CSV BOM and KiCad/Altium library generation |
| 12 | `loki` | Grafana Loki | 3100 | Log aggregation from all containers |
| 13 | `grafana` | Grafana | 3001 | Observability dashboard for logs and metrics |

External services (not containerized):
- **Supabase** -- hosted PostgreSQL (conversations, messages, agent tasks)
- **OpenAI API** -- GPT-5.4 inference (via LiteLLM proxy)
- **Nexar API** -- component search GraphQL endpoint

## Communication patterns

### Redis task queue

Backend and agent communicate exclusively through Redis -- no direct HTTP calls between them.

1. **Backend** pushes tasks to Redis list `agent:tasks` via `LPUSH`.
2. **Agent worker** picks tasks with `BLMOVE agent:tasks agent:processing` -- atomically moves the task to a processing list for crash recovery.
3. **Agent** publishes status updates to Redis pub/sub channel `agent:status:{conversation_id}`.
4. **Backend** subscribes to the pub/sub channel and forwards updates to the frontend via WebSocket.
5. On completion, agent removes the task from `agent:processing` with `LREM`.

Crash recovery: on startup, the agent checks `agent:processing` for orphaned tasks and moves them back to `agent:tasks` for reprocessing.

### Redis pub/sub message types

```json
{"task_id": "uuid", "type": "status", "text": "Analyzing page 3/6..."}
{"task_id": "uuid", "type": "result", "data": { ... }}
{"task_id": "uuid", "type": "error", "error": "LiteLLM timeout after 3 retries"}
```

### MCP tool communication (HTTP + SSE)

All MCP servers run in separate Docker containers. The agent connects to each server via HTTP+SSE transport (not stdio). Each server exposes an HTTP endpoint (e.g., `http://mcp-nexar:8001/mcp`) and a `/health` endpoint for Docker health checks.

### WebSocket (frontend <-> backend)

The backend maintains one WebSocket connection per active conversation. On connect, the backend sends the current agent task status from Supabase (if a task is running). It then subscribes to the Redis pub/sub channel and relays all updates. WebSocket is for real-time push only -- Supabase is the source of truth.

## Data flow: file upload

1. User selects a file in the frontend.
2. Frontend uploads immediately to `POST /api/upload` (before message send).
3. Backend stores file in MinIO at staging path: `uploads/{user_id}/staging/{upload_id}/{filename}`.
4. User sends message. Backend moves staged files to `uploads/{user_id}/{conversation_id}/{filename}`.
5. Backend saves the user message to Supabase with resolved attachment paths.
6. Backend publishes an agent task to Redis with the attachment MinIO URIs.

## Data flow: agent run

1. Backend receives `POST /api/conversations/{id}/messages`.
2. Backend saves the user message to Supabase, creates an `agent_tasks` row with status `running`, pushes the task to Redis queue, and returns `202 Accepted`.
3. Frontend opens a WebSocket to `/ws/conversations/{id}`.
4. Agent worker picks the task from Redis.
5. Agent calls MCP tools (e.g., `render_pdf_pages` on mcp-documents, `search_parts` on mcp-nexar).
6. Agent publishes status updates to Redis pub/sub. Backend relays them via WebSocket and persists them to `agent_tasks.current_status`.
7. Agent publishes the final result. Backend saves the assistant message to Supabase, marks the task as `completed`, and pushes the result via WebSocket.

See `spec/agent-task-flow.mermaid` for a sequence diagram.

## Data flow: browser reconnection

1. On page load, frontend fetches `GET /api/conversations` which includes `agent_status` per conversation.
2. For conversations with `running` status: frontend opens a WebSocket. Backend sends the latest status text as the first message (read from `agent_tasks.current_status` in Supabase).
3. For conversations where the agent finished while the browser was closed: the completed response is already in the `messages` table. Frontend loads it normally via `GET /api/conversations/{id}`.
4. For `failed` conversations: frontend displays the error from `agent_tasks.error`.

Nothing is lost if the browser is closed during an agent run. The agent keeps working server-side, and all state is persisted in Supabase.

## Mermaid diagrams

The `spec/` directory contains Mermaid diagrams for reference:

- `spec/architecture.mermaid` -- full system architecture graph
- `spec/agent-task-flow.mermaid` -- sequence diagram for agent task processing
- `spec/env-mapping.mermaid` -- environment variable to container mapping
