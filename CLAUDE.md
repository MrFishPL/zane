# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Web application for automated electronic component sourcing. User uploads a schematic (PDF/photo/sketch), AI agent visually analyzes it, identifies components, searches distributor APIs (Nexar/Octopart), and produces a structured BOM with pricing, stock, SnapMagic CAD model links, and downloadable CSV/KiCad/Altium exports.

## Architecture

12 Docker containers on a single network (`app-network`):

- **frontend** (Next.js :3000) — Chat UI with file upload, WebSocket status, BOM rendering
- **backend** (FastAPI :8000) — REST API, Supabase CRUD, MinIO files, Redis pub/sub → WebSocket
- **agent** (OpenAI Agents SDK worker) — Picks tasks from Redis queue, orchestrator agent delegates to sub-agents (analysis, sourcing, export), makes interactive decisions via handoffs. Direct OpenAI API (GPT-5.4).
- **redis** (Redis 7 :6379) — Task queue (`agent:tasks`) + pub/sub (`agent:status:{conv_id}`)
- **minio** (MinIO :9000/:9001) — Object storage for uploads/temp/exports
- **mcp-nexar** (:8001) — Nexar/Octopart component search (GraphQL, OAuth2)
- **mcp-snapmagic** (:8002) — CAD model availability check (web search)
- **mcp-documents** (:8003) — PDF rendering, image processing, base64 retrieval (MinIO)
- **mcp-websearch** (:8004) — Web search fallback (direct OpenAI API)
- **mcp-export** (:8005) — CSV/KiCad/Altium library generation (MinIO)
- **loki** (:3100) + **grafana** (:3001) — Observability

External: Supabase (PostgreSQL), OpenAI API (GPT-5.4), Nexar API

## Environment

All credentials in `.env` (gitignored). See `.env.example` for template.

Key vars: `OPENAI_API_KEY`, `OPENAI_MODEL`, `NEXAR_CLIENT_ID`, `NEXAR_CLIENT_SECRET`, `SUPABASE_URL`, `SUPABASE_KEY`, `REDIS_URL`, `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`

## Commands

```bash
docker compose up --build        # Start all services
docker compose up --build <svc>  # Start single service
docker compose logs -f <svc>     # Follow logs

# Python services (backend, agent, mcp-*)
cd <service> && pip install -r requirements.txt && pytest

# Frontend
cd frontend && npm install && npm test && npm run dev
```

## Nexar API Notes

- Authentication: OAuth2 client credentials → bearer token → `Authorization: Bearer <token>`
- Token endpoint: POST `https://identity.nexar.com/connect/token` with `grant_type=client_credentials`
- GraphQL endpoint: `https://api.nexar.com/graphql`
- Primary query: `supSearch` for component search

## Key Patterns

- **Backend ↔ Agent**: Redis queue (BLMOVE/LREM) + pub/sub — no direct HTTP
- **Agent ↔ Tools**: All through MCP servers (HTTP+SSE transport), never direct code execution
- **MCP servers**: Python `mcp` SDK with `FastMCP`, each exposes `/health` + `/mcp` (SSE)
- **Structured logging**: All Python services use `structlog` → JSON to stdout → Loki
- **Agent responses**: Always JSON with `status` field: `needs_clarification`, `recommendation`, or `analysis`
- **MinIO paths**: `uploads/{user_id}/{conversation_id}/`, `temp/...`, `exports/...`
- **Shared user ID**: `00000000-0000-0000-0000-000000000001` (no auth for now)
