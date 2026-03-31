# Electronics Component Sourcing Agent — Claude Code Spec

## Instructions for Claude Code

**Resolve contradictions autonomously.** This spec is large and may contain inconsistencies, ambiguous areas, or edge cases not fully covered. If you encounter a contradiction between two sections, a missing detail that blocks progress, or an ambiguity in requirements — resolve it yourself using your best engineering judgment. Do NOT stop and ask. Document your decision with a brief comment in the code or a note in the relevant doc file so the decision is traceable.

**Use subagents aggressively.** Build independent services in parallel (e.g. mcp-nexar, mcp-documents, mcp-export have zero shared code — build them simultaneously). Write tests in a subagent while the main agent builds the implementation. Generate documentation in a subagent after implementation. Frontend and backend can be developed in parallel once the API contract (endpoints + JSON schemas) is defined first.

**Commit incrementally.** Commit after completing each meaningful unit of work: finishing a service, passing tests, completing a feature. Never accumulate a massive uncommitted diff.

**Final frontend testing must use a browser.** After building the frontend, verify it works end-to-end by launching the app and testing in a real browser. Check: conversation CRUD (create, rename, delete with confirmation), file upload with progress bar, WebSocket status updates, agent response rendering (BOM table, clarification questions, analysis results), download buttons for CSV and library files, attachment display with clickable previews, sidebar spinner for active agents, reconnection after page reload.

## Project overview

Build a web application for automated electronic component sourcing. The user uploads a schematic (PDF, photo, hand-drawn sketch, or text description) and describes requirements in natural language. An AI agent visually analyzes the schematic (computer vision, NOT OCR), identifies functional blocks and components, then searches distributor APIs to find real, purchasable components matching the requirements.

The agent produces a structured JSON BOM with: MPN, price, stock, distributor link, selection justification, warnings (lifecycle, missing CAD models, stock issues), and total BOM cost summary. For each component it checks whether a symbol/footprint is available on SnapMagic and provides a link.

After BOM generation the agent automatically produces downloadable files:
- **CSV** (cart/BOM): exactly two columns — Manufacturer Part Number and quantity (multiplied by production volume). Ready for distributor cart import.
- **Library package** natively supported by KiCad or Altium Designer (symbols + footprints).

The frontend displays download buttons for these files alongside the agent's final response.

**The agent NEVER executes code directly.** All operations go through MCP tool calls. The agent only thinks, plans, and invokes tools.

The agent responds in the user's language (if the user writes in Polish, the agent responds in Polish).

---

## Architecture overview

Everything runs in Docker containers on a single Docker network (`app-network`). Containers communicate by service name.

| # | Container | Tech | Port | Role |
|---|-----------|------|------|------|
| 1 | `frontend` | React / Next.js | 3000 | Chat UI |
| 2 | `backend` | Python / FastAPI | 8000 | API server, file handling, DB, WebSocket |
| 3 | `agent` | Anthropic Agent SDK (Python) | internal | Agent worker — picks tasks from Redis, orchestrates subagents, calls MCP tools |
| 4 | `litellm-proxy` | LiteLLM Proxy Server | 4000 | Translates Anthropic format → OpenAI format |
| 5 | `redis` | Redis 7 | 6379 | Task queue + pub/sub for agent status updates |
| 6 | `minio` | MinIO | 9000 (API) + 9001 (console) | Object storage for uploads, temp files, exports |
| 7 | `mcp-nexar` | Python MCP server | 8001 | Nexar/Octopart component search |
| 8 | `mcp-snapmagic` | Python MCP server | 8002 | CAD model availability check |
| 9 | `mcp-documents` | Python MCP server | 8003 | PDF/image processing |
| 10 | `mcp-websearch` | Python MCP server | 8004 | Web search fallback |
| 11 | `mcp-export` | Python MCP server | 8005 | CSV and KiCad/Altium library generation |
| 12 | `loki` | Grafana Loki | 3100 | Log aggregation — all containers ship logs here |
| 13 | `grafana` | Grafana | 3001 | Observability dashboard — logs, metrics, traces |

External services:
- **Supabase** — hosted PostgreSQL database
- **OpenAI API** — GPT-5.4 model (accessed through LiteLLM)
- **Nexar API** — component search (GraphQL, OAuth2)

---

## Environment variables (.env)

```env
# litellm-proxy
OPENAI_API_KEY=...

# mcp-nexar
NEXAR_CLIENT_ID=...
NEXAR_CLIENT_SECRET=...

# backend
SUPABASE_URL=...
SUPABASE_KEY=...

# redis (used by: backend, agent)
REDIS_URL=redis://redis:6379/0

# litellm endpoint (used by: agent, backend, mcp-snapmagic, mcp-websearch)
LITELLM_BASE_URL=http://litellm-proxy:4000

# minio container (server credentials only)
MINIO_ROOT_USER=...
MINIO_ROOT_PASSWORD=...

# minio clients (backend, mcp-documents, mcp-export)
MINIO_ENDPOINT=minio:9000
# + MINIO_ROOT_USER and MINIO_ROOT_PASSWORD from above

# observability (all containers ship logs to Loki)
LOKI_URL=http://loki:3100
```

---

## Docker Compose

All containers share network `app-network`. Each MCP server container uses `healthcheck` with a simple HTTP ping. The `agent` container depends on all MCP servers, `litellm-proxy`, and `redis`. The `backend` container depends on `redis`, `minio`, and `litellm-proxy` (for title generation).

All containers use the Docker Loki logging driver to ship stdout to Loki. Python services additionally use `structlog` for structured JSON output (see Observability section).

Create a `docker-compose.yml` at the project root defining all 13 services with appropriate `depends_on`, environment variable passthrough from `.env`, volume mounts for agent markdown files, and port mappings.

---

## Frontend

**Framework**: React with Next.js (App Router), TypeScript, Tailwind CSS.

### Chat interface

Single chat interface. All conversations stored. No login — one shared user for now. **Design everything with multi-user support in mind** (user_id field everywhere, per-user data isolation).

**Left sidebar — conversation list:**
- New conversation is created immediately when the user sends the first message.
- Title is auto-generated using a cheap/fast model call (e.g. GPT-4o-mini via LiteLLM). Backend calls `LITELLM_BASE_URL` with model `gpt-4o-mini` (must be configured in `litellm_config.yaml` as a second model).
- On hover over a conversation item: show rename and delete buttons.
- **Rename**: inline edit, auto-saves on blur. No save button needed.
- **Delete**: show an elegant inline confirmation dialog (NOT browser `confirm()` / system alert). Smooth, minimal, integrated into the UI.
- Conversations with an active (running) agent show a spinner icon next to the title.

**Main chat area:**
- Message bubbles for user and agent messages.
- Agent responses are rendered from the JSON structure (see Agent Response Format below). The frontend parses the JSON and renders it as a rich, readable card — NOT raw JSON.
- When the agent returns `status: "recommendation"`, the frontend renders the component table, BOM summary, and download buttons for CSV and library files.
- When the agent returns `status: "needs_clarification"`, the frontend renders the questions and any annotated images inline.
- When the agent returns `status: "analysis"`, the frontend renders the identified blocks and components, and any unclear areas with annotated images.

### File upload

- **Limit**: 100MB per file. Allowed formats: PDF, PNG, JPG, JPEG, WEBP.
- File uploads to MinIO **immediately** after selection (before message send).
- **Upload path strategy**: When the user selects a file, the conversation may not exist yet (first message). The backend uploads to a staging path: `uploads/{user_id}/staging/{upload_id}/{filename}`. When the first message is sent and the conversation is created, the backend moves the staged files to `uploads/{user_id}/{conversation_id}/{filename}` and updates the attachment paths in the message payload. For subsequent messages in an existing conversation, files upload directly to `uploads/{user_id}/{conversation_id}/`.
- **Staging cleanup**: Orphaned staging files (user uploaded but never sent a message) are cleaned up by a backend startup task and a periodic job (every 1 hour) that deletes all staging files older than 24 hours.
- Frontend shows a progress bar during upload.
- The "Send" button is **disabled** while any upload is in progress. It must be impossible to send a message while a file is still uploading.
- After upload completes, the frontend stores the returned MinIO path and attaches it to the message payload on send.

### Agent status reporting

- Agent responses can take a very long time (up to 30 minutes for complex schematics).
- **Do NOT stream tokens**. Instead, the agent reports stage statuses: "Rendering PDF...", "Analyzing page 3/6...", "Searching BFP740 in Nexar...", "Checking SnapMagic...", "Generating BOM..."
- Frontend displays the current status as an animated message below the chat input.
- The user can browse and write in OTHER conversations while the agent is working. One user can have multiple conversations running in parallel.
- **The "Send" button is disabled in a conversation where an agent is currently running.** The user must wait for the agent to finish (or fail) before sending another message in that conversation. This prevents race conditions with overlapping agent runs in the same conversation.
- Conversations with an active agent are visually marked (spinner next to the name in the sidebar).

### Browser close / reconnection resilience

The user may close the browser, switch devices, or lose internet during an agent run. **Nothing is lost — the agent keeps working server-side.**

- On page load, frontend fetches `GET /api/conversations` which includes `agent_status` for each conversation (idle / running / completed / failed).
- For conversations with `running` status: frontend opens a WebSocket. The backend immediately sends the latest status text (e.g. "Analyzing page 4/6...") as the first WS message — the user sees where the agent is right now.
- For conversations where the agent finished while the browser was closed: the completed response is already in the `messages` table. Frontend loads it normally via `GET /api/conversations/{id}` — no special handling needed.
- For conversations with `failed` status: frontend shows the error message from `agent_tasks.error`.
- WebSocket auto-reconnects on disconnect (exponential backoff, max 5 retries). On each reconnect, backend re-sends the current status.

### Conversation context

The agent sees the full conversation history (previous messages and responses). The user can reference earlier results, e.g. "change the potentiometers to 10k" or "order from TME instead of Mouser". The agent responds in context of the previous BOM without re-analyzing the schematic.

### Context window management

Agent responses (especially BOM JSONs) can be very large. To avoid exceeding the LLM context window:

**What goes into conversation history sent to the agent:**
- **User messages**: full text + list of attachment paths (but NOT the file contents or base64 images).
- **Agent responses with `status: "recommendation"`**: the `message` field (human-readable summary) + a **compressed BOM summary** — a flat list with only the fields the agent needs to modify the BOM:
```json
{"bom_compact": [
  {"ref": "R1", "mpn": "RC0603FR-0710KL", "description": "10k 0603 1%", "package": "0603", "qty": 2},
  {"ref": "U1", "mpn": "LM317T", "description": "Adj vreg 1.2-37V TO-220", "package": "TO-220", "qty": 1}
]}
```
  This is ~40 tokens per component. A 30-part BOM fits in ~1200 tokens. The full `data` object (alternatives, URLs, price breaks, snapmagic formats) is stored in Supabase but NOT included in history. If the agent needs to re-price components, it queries Nexar again.
- **Agent responses with other statuses** (`analysis`, `needs_clarification`): only `status` and `message` fields.

**Backend prepares the trimmed history** before publishing to Redis. The task payload `conversation_history` contains only the trimmed version. The agent never fetches raw history from Supabase directly.

**Truncation rules:**
- Maximum 20 most recent message pairs (user + assistant) in history.
- If the conversation exceeds 20 turns, older messages are dropped (FIFO).
- Images / file contents are NEVER included in history — only MinIO paths.

---

## Backend

**Framework**: Python, FastAPI.

### Responsibilities

- Receives uploaded files from the frontend, stores them in MinIO.
- Manages conversations and messages (CRUD via Supabase).
- Publishes agent tasks to Redis queue.
- Subscribes to Redis pub/sub for agent status updates, forwards them to frontend via WebSocket.
- Serves downloadable files (CSV, libraries) from MinIO.
- Maintains WebSocket connections with the frontend for real-time status updates.
- Generates conversation titles using a cheap model call.
- Cleans up MinIO files when a conversation is deleted.

### Backend ↔ Agent communication (Redis)

The backend and agent are separate Docker containers. They communicate through Redis — no direct HTTP calls. This decouples them and handles the fact that agent runs can last up to 30 minutes.

**Protocol:**

1. **Backend publishes a task** to Redis list `agent:tasks`:
```json
{
  "task_id": "uuid",
  "conversation_id": "uuid",
  "message_id": "uuid",
  "user_id": "uuid",
  "message": "user's message text",
  "attachments": ["minio://uploads/{user_id}/{conversation_id}/schematic.pdf"],
  "conversation_history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
}
```

2. **Agent worker** picks the task from the queue (`BLMOVE agent:tasks agent:processing`) — atomically moves it to a processing list for crash recovery. It then processes the task and publishes status updates to Redis pub/sub channel `agent:status:{conversation_id}`:
```json
{
  "task_id": "uuid",
  "type": "status",
  "text": "Analyzing page 3/6..."
}
```

3. **Agent worker** publishes the final result to the same channel:
```json
{
  "task_id": "uuid",
  "type": "result",
  "data": { ... agent JSON response ... }
}
```

4. **Agent worker** publishes errors to the same channel:
```json
{
  "task_id": "uuid",
  "type": "error",
  "error": "LiteLLM timeout after 3 retries"
}
```

5. **Backend** subscribes to `agent:status:{conversation_id}` for each active conversation. On receiving a message:
   - `type: "status"` → update `agent_tasks.current_status` in Supabase + push to frontend via WebSocket.
   - `type: "result"` → save to `messages` table + update `agent_tasks` to `completed` + push to frontend.
   - `type: "error"` → update `agent_tasks` to `failed` + push to frontend.

**This architecture means:** backend never blocks waiting for the agent. The agent can crash and restart without losing the task (Redis persists the queue). Multiple agent workers can run in parallel.

### Task queue

Redis list `agent:tasks` acts as a simple FIFO queue. The agent container runs N worker coroutines that pick tasks from the queue.

**Crash recovery:** A plain `BRPOP` removes the task permanently — if the worker crashes mid-processing, the task is lost. Instead, use the following pattern:
1. Worker calls `BLMOVE agent:tasks agent:processing LEFT RIGHT` — atomically moves the task from the queue to a processing list.
2. Worker processes the task normally.
3. On completion (success or error), worker calls `LREM agent:processing 1 {task}` to remove it from the processing list.
4. **On startup**, the agent container checks `agent:processing` for orphaned tasks (from a previous crash). Any tasks found are moved back to `agent:tasks` for reprocessing. For each orphaned task, the agent publishes a status update to Redis: `{type: "status", text: "Requeued after worker restart"}` — the backend picks this up and updates `agent_tasks` in Supabase accordingly. **The agent never writes to Supabase directly.**

Current configuration: **max 50 concurrent tasks** (dummy limit, not a bottleneck for now). Configurable via env var `AGENT_MAX_CONCURRENT_TASKS=50`.

If the queue is full, backend returns HTTP 429 to the frontend with a "server busy" message. (This won't happen in practice with current limits.)

### WebSocket status reporting

- Backend maintains one WebSocket connection per conversation with the frontend.
- Backend subscribes to Redis pub/sub `agent:status:{conversation_id}` and forwards messages to the WebSocket.
- If no WebSocket is connected (user closed browser), status updates are still persisted to `agent_tasks` in Supabase — nothing is lost.

### Agent task persistence and browser reconnection

Agent runs can take up to 30 minutes. The user may close the browser, switch devices, or lose connection at any time. **Nothing must be lost.**

**All agent state is persisted to Supabase (`agent_tasks` table):**
- When backend publishes a task to Redis, it simultaneously creates a row in `agent_tasks` with status `running`.
- Every status update received from Redis pub/sub is written to `current_status` in `agent_tasks` AND pushed to frontend via WebSocket (if connected).
- When the agent finishes, backend writes the final response to `messages`, updates `agent_tasks` to `completed`, and pushes via WebSocket (if connected).
- On error, backend sets `agent_tasks` to `failed` with error details.

**Frontend reconnection flow:**
1. On page load / reconnect, frontend calls `GET /api/conversations` which returns each conversation's `agent_status` (idle / running / completed / failed).
2. For any conversation with status `running`, frontend opens a WebSocket and receives the current status immediately (backend sends the latest `current_status` from `agent_tasks` on WS connect, then subscribes to Redis pub/sub for live updates).
3. For conversations where the agent finished while the user was away: the completed message is already in `messages` table. Frontend fetches it with `GET /api/conversations/{id}` — no data is lost.
4. Sidebar shows spinner on conversations that are still `running`.

**WebSocket is for real-time push only — it is NOT the source of truth.** Supabase is the source of truth. If the WebSocket was disconnected during the entire agent run, the user still sees the complete result when they reload.

### API endpoints

```
POST   /api/conversations                    — create conversation
GET    /api/conversations                    — list conversations (includes agent_status per conversation)
GET    /api/conversations/{id}               — get conversation with messages
PATCH  /api/conversations/{id}               — update title
DELETE /api/conversations/{id}               — delete conversation + messages + all MinIO files (uploads, temp, exports for this conversation)
POST   /api/conversations/{id}/messages      — send message (publishes task to Redis queue)
GET    /api/conversations/{id}/agent-status   — get current agent task status (running/completed/failed + current_status text)
POST   /api/upload                           — upload file to MinIO, returns MinIO path
GET    /api/files/{path}                     — serve file from MinIO (for downloads and inline attachment display)
WS     /ws/conversations/{id}                — WebSocket for status updates (sends latest status on connect)
```

**Conversation deletion cleanup:** When a conversation is deleted, the backend deletes all associated MinIO files: `uploads/{user_id}/{conversation_id}/`, `temp/{user_id}/{conversation_id}/`, `exports/{user_id}/{conversation_id}/`. This happens synchronously before returning the DELETE response.

**Attachment display:** Uploaded files (images, PDFs) and agent-generated annotated images are served via `GET /api/files/{path}`. The frontend renders clickable thumbnails for image attachments inline in message bubbles. Clicking opens a lightbox/modal with the full-resolution image. PDF attachments show a clickable preview card.

### Supabase schema

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  title TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content JSONB NOT NULL,
  attachments JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE library_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  workspace_url TEXT,
  preferences JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE agent_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
  message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
  current_status TEXT,              -- latest human-readable status, e.g. "Analyzing page 3/6..."
  error TEXT,                       -- error details if status = 'failed'
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);
```

Insert a default shared user on first run:
```sql
INSERT INTO users (id, name) VALUES ('00000000-0000-0000-0000-000000000001', 'shared') ON CONFLICT DO NOTHING;
```

---

## Agent backend

### Framework and model

**Anthropic Agent SDK** (Python) with **GPT-5.4** as the LLM (accessed through LiteLLM Proxy).

The agent container runs as a **worker process** — it does NOT expose an HTTP API. It pulls tasks from Redis queue (`BLMOVE agent:tasks agent:processing`), processes them, and publishes results/status updates back to Redis pub/sub. On completion, removes the task from `agent:processing`. On startup, requeues any orphaned tasks from `agent:processing`. Multiple worker coroutines run concurrently (configurable via `AGENT_MAX_CONCURRENT_TASKS`).

**Env:** `REDIS_URL`, `LITELLM_BASE_URL`

**Why this architecture:**
- Anthropic Agent SDK: agents and subagents defined as markdown files with YAML frontmatter. Changing agent behavior = editing a text file, no code changes. Native MCP support.
- GPT-5.4 via LiteLLM: lower inference costs than Claude Opus/Sonnet at comparable quality.

**LiteLLM Proxy** runs in a separate Docker container (`litellm-proxy:4000`). It translates requests from Anthropic SDK format to OpenAI API format. The agent sees LiteLLM as its LLM endpoint.

**Fallback**: If LiteLLM causes issues with format translation (especially with subagents and complex tool calls), the fix is changing one line in LiteLLM config to point to `claude-sonnet-4-6` instead — no changes to agents or MCP servers.

**LiteLLM config** (`litellm_config.yaml`):
```yaml
model_list:
  - model_name: gpt-5.4
    litellm_params:
      model: openai/gpt-5.4
      api_key: os.environ/OPENAI_API_KEY
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
```

Start LiteLLM with: `litellm --config litellm_config.yaml --port 4000`

Read the LiteLLM docs (https://docs.litellm.ai/) to verify:
- How to configure the proxy to accept Anthropic-format requests and translate to OpenAI.
- Whether additional config is needed for tool calls / function calling translation.
- Whether any extra API keys or settings are required.

### Agent definition (markdown files)

The agent is defined via markdown files — analogous to Claude Code's structure:

```
agent/
├── CLAUDE.md                          # Main system prompt, identity, general rules
├── .claude/
│   ├── agents/                        # Subagent definitions (markdown + YAML frontmatter)
│   │   ├── schematic-analyzer.md      # Analyzes schematic images, identifies components
│   │   ├── component-sourcer.md       # Searches Nexar, compares prices/stock
│   │   ├── cad-checker.md             # Checks SnapMagic availability
│   │   └── export-generator.md        # Generates CSV and library files
│   └── rules/                         # Domain rules (separate files)
│       ├── component-selection.md     # Rules for selecting components
│       ├── data-sources.md            # Available data sources and priorities
│       ├── output-format.md           # JSON output format specification
│       └── error-handling.md          # Error handling procedures
```

**Read the Anthropic Agent SDK docs** (https://docs.anthropic.com/en/docs/agents) to understand:
- How to define agents and subagents as markdown with YAML frontmatter.
- How to configure MCP server connections.
- How to pass conversation history.
- How subagent delegation works.

### Agent behavior rules

**The agent NEVER executes code directly.** No Python scripts, no bash commands. All operations go through MCP tool calls exclusively. The agent only thinks, plans, and invokes tools.

**Modes:**
- `semi-manual` (default): agent shows proposals, user approves.
- `auto`: agent selects components autonomously (requires explicit activation by user).

**Selection priority (default):** lowest unit price. User can override to: immediate availability, quality, specific distributor.

**Subagent parallelism — the agent should aggressively use subagents for parallel execution:**
- Schematic analysis: one subagent per PDF page / functional block.
- Component sourcing: parallel search for multiple components at once.
- CAD model checking: separate subagent checking SnapMagic availability after component selection.

### Vision flow — how the agent "sees" schematics

1. Agent calls MCP tool `render_pdf_pages(pdf_path)` → `mcp-documents` renders pages to PNG (300 DPI), saves to MinIO. Returns JSON manifest with page list, classification (schematic vs text), and MinIO paths.
2. Agent calls MCP tool `get_image_base64(image_path)` on `mcp-documents` → the server fetches the image from MinIO and returns it as a base64-encoded string. **The agent does not access MinIO directly.**
3. Agent sends the base64 image as an image attachment in the next LLM query via LiteLLM.
4. GPT-5.4 analyzes the image visually (computer vision) and returns recognized components.
5. If a fragment is unreadable, the agent calls `crop_zoom` on `mcp-documents` (which returns the cropped image as base64), then repeats analysis on the zoomed-in version.
6. For annotated images sent back to the user (e.g. red rectangles marking unclear areas), the agent calls `annotate_image` which saves to MinIO and returns the MinIO path — the frontend fetches and displays it via the backend's `/api/files/{path}` endpoint.

### Error handling

- **MCP server unreachable** → agent informs the user which service is unavailable and what can be done without it.
- **Nexar returns error/quota exceeded** → agent falls back to web search and informs user about reduced data quality.
- **LiteLLM timeout** → retry with exponential backoff, max 3 attempts.
- **Agent never returns an empty response** — always explains what happened and what it did to address the issue.

### Agent response format — always JSON with one of three statuses

**Status: `needs_clarification`** — when the agent needs user input:
```json
{
  "status": "needs_clarification",
  "message": "Description of what is unclear, plain text without markdown",
  "data": {
    "questions": [
      {
        "id": 1,
        "question": "Question text",
        "default": "suggested answer"
      }
    ],
    "annotated_image": "minio://temp/{user_id}/{conversation_id}/annotated_page3.png"
  }
}
```

**Status: `recommendation`** — when the agent returns a BOM:
```json
{
  "status": "recommendation",
  "message": "Description of what was found and why, plain text without markdown",
  "data": {
    "components": [
      {
        "ref": "U1",
        "mpn": "LM317T",
        "manufacturer": "STMicroelectronics",
        "description": "Adjustable voltage regulator, 1.2-37V, 1.5A, TO-220",
        "package": "TO-220",
        "qty_per_unit": 1,
        "qty_total": 100,
        "justification": "Why this component was selected",
        "unit_price": 2.02,
        "price_break": {"qty": 100, "unit_price": 1.12},
        "stock": 24723,
        "lifecycle": "Active",
        "distributor": "Mouser",
        "distributor_url": "https://...",
        "datasheet_url": "https://...",
        "snapmagic_url": "https://www.snapeda.com/parts/...",
        "snapmagic_available": true,
        "snapmagic_formats": ["KiCad", "Altium", "Eagle"],
        "mpn_confidence": "verified",
        "verified": true,
        "warnings": ["warning text if any"],
        "alternatives": [
          {
            "mpn": "LM317TG",
            "manufacturer": "onsemi",
            "unit_price": 2.78,
            "note": "Why this alternative"
          }
        ]
      }
    ],
    "not_sourced": [
      {
        "item": "Item description",
        "reason": "Why not found"
      }
    ],
    "bom_summary": {
      "unique_parts": 8,
      "total_components_per_unit": 10,
      "cost_per_unit": 33.56,
      "cost_total": 3356.00,
      "volume": 100,
      "currency": "USD"
    },
    "export_files": {
      "csv": "minio://exports/{user_id}/{conversation_id}/bom_2024-01-15.csv",
      "kicad_library": "minio://exports/{user_id}/{conversation_id}/library_2024-01-15.zip",
      "altium_library": "minio://exports/{user_id}/{conversation_id}/library_altium_2024-01-15.zip"
    },
    "sources_queried": ["Nexar/Octopart"]
  }
}
```

**Status: `analysis`** — intermediate schematic analysis:
```json
{
  "status": "analysis",
  "message": "Description of what was recognized, plain text without markdown",
  "data": {
    "blocks": [
      {
        "name": "Power supply section",
        "components": ["LM317", "2x 470uF", "2x 100nF"],
        "page": 1
      }
    ],
    "identified_components": ["LM317", "CD4069", "BC547", "1N4148"],
    "unclear_areas": [
      {
        "page": 2,
        "description": "Unreadable resistor value near Q1",
        "annotated_image": "minio://temp/{user_id}/{conversation_id}/annotated_page2.png"
      }
    ]
  }
}
```

**Format rules:**
- `message` field: always plain text, NEVER markdown (no **bold**, no headings, no lists).
- Null/empty fields: always include the field, use `null` for missing values, `[]` for empty arrays.
- Every component has an identical set of fields — no exceptions.
- Prices always in USD.
- `mpn_confidence`: `"verified"` (from API), `"searched"` (from web search), `"estimated"` (agent guessing).

---

## MCP Servers

All agent tools are exposed as MCP servers. The agent has no filesystem or shell access — only MCP tools.

**Transport: HTTP + SSE (Server-Sent Events).** All MCP servers run in separate Docker containers, so `stdio` transport is NOT possible. Each MCP server exposes an HTTP endpoint (e.g. `http://mcp-nexar:8001/mcp`). The agent connects to each server via HTTP+SSE transport as configured in the Anthropic Agent SDK MCP server list. Ensure each MCP server implements the MCP HTTP+SSE transport spec from the Python MCP SDK.

### 1. mcp-nexar (Nexar/Octopart)

**The sole source of component data.** Nexar is an aggregator — returns prices and stock from Mouser, DigiKey, Farnell, Newark, and many other distributors in a single query. Paid plan: 2000 matched parts/month.

**Env:** `NEXAR_CLIENT_ID`, `NEXAR_CLIENT_SECRET`

**Tools:**
- `search_parts(query: str)` — descriptive search (e.g. "3 ohm resistor 0603"). Returns top 5 results with key specs, top 5 sellers, max 3 price breaks each.
- `search_mpn(mpn: str)` — search by manufacturer part number. Same response compression.
- `multi_match(mpns: list[str])` — batch lookup of multiple MPNs at once.
- `check_lifecycle(mpn: str)` — returns `active`, `nrnd`, `obsolete`, or `unknown`.
- `get_quota_status()` — returns remaining matched parts this month.

**Implementation:** GraphQL API, OAuth2 client credentials flow. Compress responses to essential data (top 5 sellers, max 3 price breaks, key specs only).

**IMPORTANT FOR DEVELOPER:** Before building this MCP server, read the official Nexar API documentation: https://support.nexar.com/support/solutions/101000253221 — GraphQL schema, authentication, query examples, rate limits.

### 2. mcp-snapmagic (CAD model availability)

Checks availability of symbols, footprints, and 3D models on SnapMagic (formerly SnapEDA).

**NOTE:** SnapMagic does NOT have a public API. This MCP server is currently implemented as an internal agent that uses the Anthropic SDK's built-in `web_search` tool (via LiteLLM) to search for component pages on SnapMagic (e.g. "LM317T site:snapeda.com KiCad"). Eventually it will be replaced with an official API after negotiating access with SnapMagic. **The MCP interface (tool names, parameters, response format) must be designed so that swapping the implementation from web search to API requires NO changes on the agent side.**

**Env:** `LITELLM_BASE_URL` (to call the LLM with web_search tool)

**Tools:**
- `check_cad_availability(mpn: str, format: str)` — checks if a symbol/footprint exists on SnapMagic for a given MPN. Format: `"kicad"`, `"altium"`, `"eagle"`, or `"any"`. Returns: `{available: bool, url: str, formats: list[str]}`.
- `check_cad_batch(mpns: list[str], format: str)` — batch lookup for multiple MPNs. Returns list of results.

**Temporary implementation:** internal agent with Anthropic SDK `web_search` tool, accessed via LiteLLM. Does NOT scrape SnapMagic page content — only checks in search results whether a product page exists and what formats are mentioned in the snippet.

### 3. mcp-documents (Document Processing)

The agent's "eyes" — prepares visual materials for the LLM's vision analysis.

**Env:** `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`

**Tools — PDF:**
- `render_pdf_pages(pdf_path: str)` — renders all PDF pages to PNG (300 DPI), saves to MinIO. Returns JSON manifest with page list and classification (schematic vs text).
- `render_pdf_page(pdf_path: str, page_number: int)` — renders a single page.
- `classify_page(pdf_path: str, page_number: int)` — checks if page contains graphics (schematic) or just text (license, tabular BOM). Returns `"schematic"` or `"text"`.
- `extract_text(pdf_path: str, page_number: int)` — extracts text from a text page (native PDF text extraction, not OCR).

**Tools — Image:**
- `get_image_base64(image_path: str)` — fetches an image from MinIO and returns it as a base64-encoded string. Used by the agent to send images to the LLM for vision analysis. The agent does NOT access MinIO directly — all image retrieval goes through this tool.
- `crop_zoom(image_path: str, x1_pct: float, y1_pct: float, x2_pct: float, y2_pct: float)` — crops a rectangular region and renders at higher resolution (600 DPI). Coordinates in percentages (0-100). Returns the cropped image as **base64** (for agent to send to LLM) AND saves to MinIO (for user display). Returns: `{base64: str, minio_path: str}`.
- `annotate_image(image_path: str, rectangles: list[dict])` — draws red rectangles with labels on a copy of the image. For marking unclear areas. Rectangles: `[{x1, y1, x2, y2, label}]`. Saves to MinIO, returns MinIO path (for frontend display, NOT for LLM vision).
- `get_image_info(image_path: str)` — returns dimensions, format, file size.

**Tools — temp file management:**
- `list_temp_files()` — lists files in temp directory.
- `cleanup_temp()` — cleans temp directory.

**Dependencies:** PyMuPDF, Pillow. Reads/writes files through MinIO (not local filesystem).

### 4. mcp-websearch (Web Search Fallback)

Fallback when Nexar API returns no results. Uses the Anthropic SDK's built-in `web_search` tool (via LiteLLM) to search distributor sites and extract product information.

**Env:** `LITELLM_BASE_URL` (to call the LLM with web_search tool)

**Tools:**
- `search_distributor(query: str, site: str)` — searches a specific distributor site (site:mouser.com, site:digikey.com, site:lcsc.com) using the SDK's web_search tool.
- `fetch_product_page(url: str)` — fetches product page content via web_search and extracts price, stock, MPN.

Results are marked as `mpn_confidence: "searched"` (not `"verified"` like API results).

### 5. mcp-export (BOM Export)

Generates downloadable files. Key MVP functionality.

**Env:** `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`

**Tools:**
- `generate_csv(components: list[dict], volume: int, user_id: str, conversation_id: str)` — generates CSV BOM with exactly two columns: Manufacturer Part Number and required quantity (multiplied by production volume). Ready for distributor cart import. Saves to MinIO at `exports/{user_id}/{conversation_id}/`, returns path.
- `generate_kicad_library(components: list[dict], user_id: str, conversation_id: str)` — generates a library package natively supported by KiCad (.kicad_sym + .kicad_mod). Symbols and footprints for all BOM components. Saves ZIP to MinIO at `exports/{user_id}/{conversation_id}/`, returns path.
- `generate_altium_library(components: list[dict], user_id: str, conversation_id: str)` — generates a library package natively supported by Altium Designer (.SchLib + .PcbLib). Saves ZIP to MinIO at `exports/{user_id}/{conversation_id}/`, returns path.

### MCP architecture note

GPT-5.4 does NOT natively support MCP. Anthropic Agent SDK supports MCP. LiteLLM Proxy translates requests between Anthropic Agent SDK and OpenAI API (GPT-5.4). MCP tools are loaded by Anthropic Agent SDK and passed to the model as function calls.

**Flow:** Backend → Agent (Anthropic Agent SDK) → LiteLLM Proxy (separate container) → OpenAI API (GPT-5.4) → model invokes tool → Anthropic Agent SDK → MCP server → response

---

## MinIO bucket structure

```
uploads/{user_id}/staging/{upload_id}/   — temporary staging for files uploaded before conversation is created
uploads/{user_id}/{conversation_id}/     — user-uploaded files (PDF, images), moved from staging on first message
temp/{user_id}/{conversation_id}/        — temporary files (rendered pages, annotated images)
exports/{user_id}/{conversation_id}/     — downloadable files (CSV, library ZIPs)
```

Every path is prefixed with `user_id` and `conversation_id` for future multi-user isolation AND per-conversation cleanup on delete. Staging files are moved to the conversation path when the first message is sent. Currently use the shared user ID `00000000-0000-0000-0000-000000000001`.

---

## Observability

All Python services use `structlog` to output structured JSON logs to stdout. Docker collects stdout from all containers and ships it to Loki via the Docker Loki logging driver. Grafana provides the dashboard for querying and visualizing logs.

**Two layers:**
- **Application layer**: `structlog` in every Python service — formats logs as JSON with fields like `timestamp`, `level`, `service`, `conversation_id`, `task_id`, `tool_name`, `duration_ms`.
- **Infrastructure layer**: Docker Loki logging driver — collects stdout from all containers (including non-Python ones like Redis, MinIO, frontend) and ships to Loki. No additional config per service.

### Setup

**Loki** (`loki:3100`): receives logs from all containers. Runs with a minimal `loki-config.yaml` (local storage, 7 day retention). No env vars needed.

**Grafana** (`grafana:3001`): pre-provisioned with Loki as a datasource (provisioning YAML mounted as a volume). Default login: admin/admin.

**Docker Compose logging config** (applied to every service):
```yaml
logging:
  driver: loki
  options:
    loki-url: "http://localhost:3100/loki/api/v1/push"
    labels: "service"
    loki-pipeline-stages: |
      - json:
          expressions:
            level: level
            msg: msg
```

### Structured logging

Every MCP tool call logs: tool name, input params (truncated to 200 chars), duration, success/error. Every agent task logs: start, each status update, completion/error, total duration. Every HTTP request to the backend logs: method, path, status code, duration.

### Grafana dashboards (pre-provisioned)

Create one JSON dashboard file mounted into Grafana that includes:
- **Agent tasks**: running/completed/failed counts, average duration, status timeline.
- **MCP tool calls**: call count per tool, error rate, latency p50/p95.
- **Errors**: filterable error log panel across all services.
- **Queue depth**: Redis `agent:tasks` list length (via a simple periodic log from backend).

---

## Testing requirements

Every microservice (container) must have a comprehensive test suite. Tests are required BEFORE a component is considered complete.

### Per-service requirements

**All Python MCP servers:**
- **Unit tests** — every tool/endpoint tested with mocks (no real APIs). Fixtures with example API responses. Cover edge cases: empty responses, auth errors, rate limiting, timeouts, malformed data.
- **Integration tests** — test full flow through the MCP server with mocked HTTP. Verify response compression works correctly, JSON formats are consistent.
- **Live smoke tests** — minimum 1-2 tests against real APIs (marked as `slow`/`live`, optional in CI). Budget: max 3 requests per service. Skipped gracefully when credentials are missing.

**Frontend:**
- React component tests (rendering, interactions: rename, delete, new conversation, file upload progress, status display).
- Framework: vitest or jest.

**Backend:**
- FastAPI endpoint tests, file upload, Supabase communication (mocked).
- Framework: pytest.

**Agent:**
- Workflow tests: verify the agent calls the right tools in the right order for given scenarios. Mocked MCP responses.
- Framework: pytest.

**mcp-export specifically:**
- Verify CSV has exactly 2 columns (MPN, qty).
- Verify KiCad library is syntactically correct (.kicad_sym format validation).
- Verify Altium library structure is correct.

### Test structure

Each service has a `tests/` directory with fixtures in `tests/fixtures/`. Every service must be testable with `pytest` (Python) or `npm test` (frontend) without additional configuration.

---

## Example use case (for testing and validation)

User uploads a schematic of a low-noise LNA (photo from a datasheet) and writes:

> The schematic shows a low-noise LNA. All components must operate in -20 to 100°C temperature range. Use angled SMA connectors at input and output. Choose modern SMD equivalents for the transistors on the schematic. All components except the 1µH inductor should be 0603 size. Resistor tolerance 1%, capacitor tolerance any. Choose some power connector resistant to flooding. When selecting components, aim for the lowest price for 100 units.

**Expected agent behavior:**
1. Visually recognize from the schematic: BFP740 ×2, resistors (450Ω, 620Ω, 50Ω, 220Ω, 2.2Ω), capacitors (1nF ×2), inductor 1µH.
2. Ask about ambiguities (e.g. is "50" near the output a resistor or output impedance?).
3. Search components via Nexar API — prices and stock from multiple distributors at once.
4. Propose modern SMD replacements for BFP740 (e.g. BFP740F or another SiGe LNA transistor).
5. Check symbol/footprint availability on SnapMagic for each component.
6. Return complete BOM in JSON with prices, links, and warnings.
7. Automatically generate CSV (cart for distributor import) and KiCad/Altium library package — frontend displays download buttons.

---

## Documentation references

Actively consult these during development:

- **Nexar API**: https://support.nexar.com/support/solutions/101000253221 — GraphQL schema, authentication, query examples, limits. Read BEFORE building mcp-nexar.
- **Anthropic Agent SDK**: https://docs.anthropic.com/en/docs/agents — agent framework docs, MCP integration, subagents.
- **LiteLLM**: https://docs.litellm.ai/ — proxy server config, Anthropic → OpenAI translation, model routing.
- **Supabase Python**: https://supabase.com/docs/reference/python/introduction — Python client for Supabase.
- **MCP SDK (Python)**: https://github.com/modelcontextprotocol/python-sdk — building MCP servers in Python.

---

## Project structure

```
project-root/
├── docker-compose.yml
├── .env
├── .env.example                    # Template with all required env vars (no secrets)
├── .gitignore
├── README.md                       # Project overview, quick start, architecture summary
├── docs/
│   ├── ARCHITECTURE.md             # Detailed architecture doc (from Mermaid diagrams)
│   ├── SETUP.md                    # Full setup guide: prerequisites, env vars, first run
│   ├── DEVELOPMENT.md              # Dev workflow: how to add MCP tools, modify agents, run tests
│   ├── API.md                      # Backend REST API reference
│   ├── AGENT.md                    # Agent behavior, subagents, context management
│   └── TROUBLESHOOTING.md          # Common issues, debugging, log queries
├── frontend/
│   ├── Dockerfile
│   ├── package.json
│   ├── src/
│   │   ├── app/                    # Next.js App Router
│   │   ├── components/
│   │   │   ├── ChatSidebar.tsx
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── BOMTable.tsx
│   │   │   ├── FileUpload.tsx
│   │   │   ├── StatusIndicator.tsx
│   │   │   ├── DeleteConfirmation.tsx
│   │   │   ├── InlineRename.tsx
│   │   │   ├── AttachmentPreview.tsx  # Clickable thumbnails, lightbox for images
│   │   │   └── ImageLightbox.tsx
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts     # Auto-reconnect with exponential backoff
│   │   │   └── useFileUpload.ts
│   │   └── lib/
│   │       └── api.ts
│   └── tests/
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── routers/
│   │   ├── conversations.py
│   │   ├── messages.py
│   │   ├── upload.py
│   │   └── files.py
│   ├── services/
│   │   ├── supabase_client.py
│   │   ├── minio_client.py
│   │   ├── redis_client.py         # Task publishing, pub/sub subscription
│   │   └── task_manager.py         # Orchestrates task lifecycle: publish → subscribe → persist
│   ├── websocket/
│   │   └── manager.py
│   └── tests/
├── agent/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                     # Worker entrypoint: BLMOVE loop + task processing
│   ├── worker.py                   # Redis worker: picks tasks, runs agent, publishes results
│   ├── CLAUDE.md                   # Main system prompt
│   ├── .claude/
│   │   ├── agents/
│   │   │   ├── schematic-analyzer.md
│   │   │   ├── component-sourcer.md
│   │   │   ├── cad-checker.md
│   │   │   └── export-generator.md
│   │   └── rules/
│   │       ├── component-selection.md
│   │       ├── data-sources.md
│   │       ├── output-format.md
│   │       └── error-handling.md
│   └── tests/
├── litellm-proxy/
│   ├── Dockerfile
│   └── litellm_config.yaml
├── redis/
│   └── redis.conf                  # Custom Redis config (optional, for persistence settings)
├── observability/
│   ├── loki-config.yaml
│   ├── grafana/
│   │   ├── provisioning/
│   │   │   ├── datasources/
│   │   │   │   └── loki.yaml       # Auto-provision Loki as datasource
│   │   │   └── dashboards/
│   │   │       └── dashboard.yaml  # Dashboard provisioning config
│   │   └── dashboards/
│   │       └── agent-overview.json # Pre-built Grafana dashboard
│   └── docker-compose.observability.yml  # Optional: can be merged into main compose
├── mcp-nexar/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py
│   ├── nexar_client.py
│   ├── auth.py
│   └── tests/
│       ├── test_tools.py
│       ├── test_integration.py
│       ├── test_live.py
│       └── fixtures/
├── mcp-snapmagic/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py
│   ├── search_client.py
│   └── tests/
│       ├── test_tools.py
│       └── fixtures/
├── mcp-documents/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py
│   ├── pdf_processor.py
│   ├── image_processor.py
│   ├── minio_client.py
│   └── tests/
│       ├── test_pdf.py
│       ├── test_image.py
│       └── fixtures/
├── mcp-websearch/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py
│   ├── search_client.py
│   └── tests/
│       ├── test_tools.py
│       └── fixtures/
└── mcp-export/
    ├── Dockerfile
    ├── requirements.txt
    ├── server.py
    ├── csv_generator.py
    ├── kicad_generator.py
    ├── altium_generator.py
    ├── minio_client.py
    └── tests/
        ├── test_csv.py
        ├── test_kicad.py
        ├── test_altium.py
        └── fixtures/
```

---

## Build order

Build and test each container independently before integration:

1. **Infrastructure** — MinIO (verify buckets created on startup), Redis (verify connection), Loki + Grafana (verify log ingestion).
2. **Supabase** — run schema migration, verify connection from Python.
3. **mcp-documents** — build first since it's self-contained (no external APIs). Test PDF rendering and image processing.
4. **mcp-export** — build next (also self-contained). Test CSV and library generation.
5. **mcp-nexar** — requires Nexar credentials. Build with mocked responses first, add live smoke tests.
6. **mcp-snapmagic** — web search based. Build and test.
7. **mcp-websearch** — web search fallback. Build and test.
8. **litellm-proxy** — configure and test translation.
9. **agent** — wire up Redis worker, all MCP servers, test workflows with mocked responses.
10. **backend** — API server, Redis pub/sub, WebSocket, connect to Supabase and MinIO.
11. **frontend** — chat UI, WebSocket reconnection, file upload, attachment display.
12. **Integration testing** — end-to-end flow.
13. **Documentation** — README, setup guide, API docs.

---

## Documentation

Generate the following documentation files during development. Keep them updated as the codebase evolves.

### README.md (project root)

- Project description (1 paragraph).
- Architecture diagram (link to Mermaid file or inline).
- Quick start: `cp .env.example .env`, fill in secrets, `docker compose up`.
- Links to detailed docs in `docs/`.

### docs/SETUP.md

- Prerequisites: Docker, Docker Compose, Node.js (for frontend dev).
- Step-by-step: clone → env vars → first run → verify health.
- **CORS configuration**: backend must set `Access-Control-Allow-Origin` for `http://localhost:3000` (dev) and the production frontend URL. Document this in FastAPI middleware setup.
- **MinIO bucket initialization**: document how buckets (`uploads`, `temp`, `exports`) are auto-created on first startup (backend init script or MinIO entrypoint).
- **File validation**: backend validates uploads server-side — max 100MB, allowed MIME types only (application/pdf, image/png, image/jpeg, image/webp). Reject anything else with 415 Unsupported Media Type.
- Environment variables table: every var, which container uses it, example value.

### docs/ARCHITECTURE.md

- Full architecture description with Mermaid diagrams.
- Container communication patterns (Redis queue, pub/sub, MCP).
- Data flow diagrams for key scenarios (file upload, agent run, reconnection).

### docs/DEVELOPMENT.md

- How to add a new MCP tool: create function, register in server.py, write tests, update agent rules.
- How to modify agent behavior: edit markdown files, no code changes.
- How to add a new subagent: create markdown file with YAML frontmatter in `.claude/agents/`.
- Running tests: `pytest` per service, `npm test` for frontend.
- Viewing logs: Grafana at `http://localhost:3001`, useful LogQL queries.

### docs/API.md

- All REST endpoints with request/response examples.
- WebSocket protocol: message types, reconnection behavior.
- Error response format.

### .env.example

```env
# LiteLLM Proxy — required for LLM inference
OPENAI_API_KEY=sk-...

# Nexar — required for component search (get from nexar.com)
NEXAR_CLIENT_ID=
NEXAR_CLIENT_SECRET=

# Supabase — required for database (get from supabase.com)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJ...

# Redis — internal, no change needed
REDIS_URL=redis://redis:6379/0

# LiteLLM endpoint — internal, no change needed (used by agent, backend, mcp-snapmagic, mcp-websearch)
LITELLM_BASE_URL=http://litellm-proxy:4000

# MinIO — internal, change passwords for production
MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin

# Observability — internal, no change needed
LOKI_URL=http://loki:3100

# Agent — internal
AGENT_MAX_CONCURRENT_TASKS=50
```

---

## Git workflow

**Initialize a git repository at project start.** All work must be committed incrementally.

### Commit strategy

- **Agents (Claude Code) must commit after completing each meaningful unit of work**: finishing a service, passing tests, completing a feature.
- Commit messages follow conventional commits: `feat(mcp-nexar): implement search_parts tool`, `fix(backend): handle MinIO connection timeout`, `docs: add setup guide`.
- Never commit secrets, `.env` files, or `node_modules`. The `.gitignore` must be created first.

### Subagent usage during development

**Claude Code should aggressively use subagents for parallel work:**
- Building independent MCP servers (mcp-nexar, mcp-documents, mcp-export) can happen in parallel — they share no code.
- Frontend and backend can be developed in parallel once the API contract is defined.
- Tests can be written by a subagent while the main agent builds the implementation.
- Documentation can be generated by a subagent after implementation is complete.

### .gitignore

```
.env
node_modules/
__pycache__/
*.pyc
.pytest_cache/
.next/
dist/
build/
*.egg-info/
.DS_Store
minio-data/
redis-data/
grafana-data/
loki-data/
```
