# Agent Rewrite: Orchestrator + Sub-Agent Architecture

## Problem

The current agent uses a monolithic 25-iteration tool loop (OpenAI SDK -> LiteLLM -> GPT-5.4) that suffers from:
- Tool loop losing track and making bad tool choices
- Context overflow from accumulated tool results and images
- Poor BOM quality requiring heavy post-processing (62 bug-fix commits)
- Fragile system prompt where small changes cause regressions
- No interactive decision points (fire-and-forget only)

## Solution

Replace the monolithic agent with an orchestrator + sub-agent architecture inspired by Claude Code's agent pattern. The orchestrator runs discrete phases. Search sub-agents run in parallel with isolated contexts. The agent can pause mid-execution to ask the user for decisions.

## Architecture

```
User Message + Attachments
        |
   Redis Queue (unchanged)
        |
   ORCHESTRATOR
    |
    |-- Phase 1: Parse attachments (deterministic)
    |-- Phase 2: Analyze schematic (single LLM call)
    |-- Phase 3: Search components (parallel sub-agents)
    |-- Phase 4: Check CAD availability (deterministic)
    |-- Phase 5: User decisions (interactive pause/resume)
    |-- Phase 6: Assemble BOM (deterministic)
    |-- Phase 7: Generate exports (deterministic)
        |
   Redis Pub/Sub -> Backend -> WebSocket -> Frontend
```

## Orchestrator Phases

### Phase 1: Parse Attachments (No LLM)

Calls mcp-documents tools programmatically via MCPRouter (no LLM decision-making):
- `render_pdf_pages(pdf_path)` — convert PDF to PNG images, returns list of page paths
- For each page returned, call `extract_text(pdf_path, page_number)` — collect text from every page
- `get_image_base64(image_path)` — retrieve images for LLM analysis

Output: list of image base64 strings + extracted text blocks per page.

### Phase 2: Analyze Schematic (Single LLM Call)

One call to GPT-5.4 with xhigh reasoning. Input: schematic images + extracted text + user message (constraints, priority, volume). Output: structured JSON component list.

Example output:
```json
{
  "components": [
    {
      "ref": "U1",
      "type": "op-amp",
      "description": "Low-noise operational amplifier",
      "value": "OPA1612",
      "package": "SOIC-8",
      "constraints": {"noise": "<1.1 nV/rtHz", "supply": "+/-15V"},
      "quantity_per_unit": 2
    },
    {
      "ref": "R1-R4",
      "type": "resistor",
      "value": "10k",
      "package": "0603",
      "tolerance": "1%",
      "quantity_per_unit": 4
    }
  ],
  "production_volume": 1000,
  "priority": "availability",
  "context": "audio mixer, low-noise input stage"
}
```

The LLM receives a focused prompt: analyze the schematic, identify all components, extract values/packages/constraints, structure as JSON. No tool calling — just vision + reasoning.

Note: The iterative crop-zoom workflow from the current agent is replaced by a single high-reasoning LLM call. The `crop_zoom_image` tool is not used by the orchestrator — GPT-5.4 with xhigh reasoning and full-resolution schematic images should identify components without iterative zooming.

### Phase 3: Search Components (Parallel Sub-Agents)

For each component in the list, dispatch an independent search sub-agent. Sub-agents run concurrently via `asyncio.gather(*tasks, return_exceptions=True)` — exceptions from individual sub-agents do not crash the entire batch.

Concurrency control: `asyncio.Semaphore(5)` limits concurrent sub-agents to avoid Nexar API rate limits. A BOM with 30 components will process in 6 batches of 5.

Each sub-agent:
- Gets its own fresh `AsyncOpenAI` context (no shared history)
- Has a focused system prompt: "Find the best real component matching this spec"
- Runs a tool loop (max 10 iterations)
- Returns a structured result or "not_found" with reason

Sub-agent tool set (via MCPRouter):
- `search_parts(query)` — keyword search via `supSearch` (mcp-nexar)
- `search_mpn(mpn)` — MPN lookup via `supSearchMpn` (mcp-nexar) — currently uses `supSearch`, must be changed to `supSearchMpn` for better MPN matching
- `search_distributor(query, site)` — web search fallback (mcp-websearch, only when Nexar returns nothing)
- `fetch_product_page(url)` — extract data from distributor page (mcp-websearch, fallback only)

Excluded mcp-nexar tools (intentional):
- `check_lifecycle` — REMOVED. Requires `specs` field which is behind TECH_SPECS paywall (unauthorized on current plan). Will always return "unknown". Lifecycle filtering is not possible with current subscription.
- `multi_match` — not exposed to sub-agents (they search one component at a time), but see Phase 3 optimization below
- `get_part_details` — `search_mpn` already returns sufficient detail
- `check_stock` — stock data is included via `totalAvail` field + per-seller `inventoryLevel`
- `get_quota_status` — not exposed to sub-agents; orchestrator calls it once before Phase 3

**Phase 3 optimization: batch pre-search with `supMultiMatch`**

Before dispatching individual sub-agents, the orchestrator can batch-search all components that have a known MPN (from schematic analysis) using the native `supMultiMatch` GraphQL query. This:
- Returns 3 parts per MPN in a single API call (vs 5 per individual search)
- Consumes fewer parts from monthly quota
- Provides instant results for components with exact MPNs

Components found via `supMultiMatch` skip the sub-agent tool loop entirely. Only components without a clear MPN or that were not found in the batch go through sub-agent search.

Sub-agent search strategy:
1. Search Nexar by description/value/package via `supSearch`
2. Evaluate results using `shortDescription` and LLM knowledge (note: `specs` field is unauthorized, so constraints cannot be verified via API — the LLM must reason about the description)
3. If no good match, try different keywords or broaden search
4. If still nothing, try web search fallback via mcp-websearch
5. Return best match with full pricing/stock data

Error handling per sub-agent:
- Timeout → return `{"status": "error", "ref": "U1", "reason": "search timed out"}`
- Nexar rate limit → wait 5s, retry once, then fall back to web search
- Unhandled exception → caught by gather, logged, returns error result

Sub-agent output:
```json
{
  "status": "found",
  "ref": "U1",
  "mpn": "OPA1612AIDR",
  "manufacturer": "Texas Instruments",
  "description": "Operational Amplifier, 2 Func, 500uV Offset-Max, BIPolar, PDSO8",
  "unit_price": 4.23,
  "currency": "USD",
  "total_stock": 725546,
  "distributor": "DigiKey",
  "distributor_stock": 8674,
  "distributor_url": "https://octopart.com/opatz8j6/...",
  "octopart_url": "https://octopart.com/part/texas-instruments/OPA1612AIDR",
  "median_price_1000": {"price": 4.23, "currency": "USD"},
  "constraints_reasoning": "shortDescription confirms dual op-amp in SOIC-8; TI OPA1612 datasheet specifies 1.1 nV/rtHz noise"
}
```

### Phase 4: Check CAD Availability (No LLM)

Deterministic batch call to mcp-snapmagic `check_cad_batch` with all found MPNs. Returns availability status per MPN for KiCad and Altium formats.

Note: The current `check_cad_batch` implementation inside mcp-snapmagic is sequential (calls one MPN at a time). As part of the mcp-snapmagic simplification, `check_cad_batch` should be parallelized internally using `asyncio.gather()` for faster batch lookups.

### Phase 5: User Decisions (Interactive Pause/Resume)

If any components have issues (missing CAD models, ambiguous specs, not found), the orchestrator pauses for user input.

**Pause mechanism (serialize-and-exit):**

1. Publishes `decision_required` message via Redis pub/sub:
```json
{
  "task_id": "...",
  "type": "decision_required",
  "decisions": [
    {
      "decision_id": "d1",
      "ref": "U3",
      "mpn": "LM386N",
      "issue": "no_cad_model",
      "question": "LM386N has no 3D model on SnapMagic",
      "options": [
        {"key": "A", "label": "Add without 3D model"},
        {"key": "B", "label": "Use LM386M-1 (has model, $0.15 more)", "mpn": "LM386M-1"}
      ]
    }
  ]
}
```

2. Serializes full orchestrator state to Redis hash `agent:task_state:{task_id}`:
   - Component list (Phase 2 output)
   - Search results (Phase 3 output)
   - CAD availability (Phase 4 output)
   - Current phase: `"awaiting_decision"`
   - Pending decisions list
3. Exits the coroutine, releases worker semaphore slot — truly non-blocking
4. Task moves from `agent:processing` to a new `agent:paused` list

**Resume mechanism (re-dispatch):**

The worker main loop has a secondary listener that polls `agent:paused` tasks with BRPOP on their `agent:decisions:{task_id}` keys (no Redis keyspace notification config needed). When a decision arrives:

1. Worker pops decision from `agent:decisions:{task_id}`
2. Loads serialized state from `agent:task_state:{task_id}`
3. Acquires semaphore slot
4. Creates new orchestrator coroutine starting at Phase 5 (apply decisions)
5. Orchestrator applies decisions, continues to Phase 6-7
6. Cleans up `agent:task_state:{task_id}` and `agent:paused` entry

**Timeout:** A background task checks `agent:paused` every 60s. Tasks paused > 30 minutes get auto-resumed with default choices (first option for each decision), noted in the BOM.

**Container restart recovery:** On startup, worker checks `agent:paused` list. For each paused task, checks if a decision has arrived (in `agent:decisions:{task_id}`). If yes, resumes. If no, re-publishes the `decision_required` message so the frontend can re-render it.

### Phase 6: Assemble BOM (No LLM)

Pure Python. Merges:
- Component specs from Phase 2
- Search results from Phase 3
- CAD availability from Phase 4
- User decisions from Phase 5

Produces final BOM structure with all fields populated.

### Phase 7: Generate Exports (No LLM)

Deterministic calls to mcp-export:
- `generate_csv` — BOM with MPN + quantity (quantity_per_unit * production_volume)
- `generate_kicad_library` — KiCad symbol/footprint library
- `generate_altium_library` — Altium library

Attaches export file paths to result.

## LLM Client

Direct connection to OpenAI API (no LiteLLM proxy):

```python
client = AsyncOpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
)
```

Two distinct call profiles:

**Phase 2 (schematic analysis) — heavy, vision + reasoning:**
```python
response = await client.chat.completions.create(
    model=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
    messages=messages,          # Includes image content blocks
    reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "high"),
    timeout=180                 # Vision + reasoning is slow
)
```

**Phase 3 (search sub-agent) — lighter, tool calling:**
```python
response = await client.chat.completions.create(
    model=os.environ.get("OPENAI_MODEL", "gpt-5.4"),
    messages=messages,
    tools=tools,
    tool_choice="auto",
    reasoning_effort=os.environ.get("OPENAI_REASONING_EFFORT", "high"),
    timeout=60                  # Faster — no vision, focused task
)
```

`reasoning_effort` is configurable via `OPENAI_REASONING_EFFORT` env var (default: `"high"`). If the model does not support it, the parameter is silently ignored by wrapping in a try/except that retries without it.

Retry strategy (escalating per-request timeouts, not sleep between retries):
- Phase 2: 3 attempts with 180s/360s/720s per-request timeout (vision calls are slow)
- Phase 3: 3 attempts with 60s/120s/240s per-request timeout (search calls are lighter)

On timeout, retry immediately with the next timeout tier. On non-timeout errors, do not retry.

## Interactive Pause/Resume Flow

```
Orchestrator (pausing)
  |-- Publish {type: "decision_required", decisions: [...]} to pub/sub
  |-- Serialize state to Redis hash agent:task_state:{task_id}
  |-- Move task to agent:paused list
  |-- Exit coroutine, release semaphore

Backend (WebSocket Manager)
  |-- Receives decision_required from pub/sub
  |-- Persists to Supabase as message with type "decision_required"
  |-- Forwards to frontend via WebSocket

Frontend
  |-- Renders decision cards with clickable options
  |-- User clicks option
  |-- POST /api/conversations/{id}/messages with {decision_id, choice}

Backend (messages router)
  |-- Detects decision_id in message payload
  |-- Pushes to agent:decisions:{task_id} via Redis LPUSH
  |-- Does NOT create a new agent task

Worker (decision listener)
  |-- Detects new decision in agent:decisions:{task_id}
  |-- Loads state from agent:task_state:{task_id}
  |-- Acquires semaphore, spawns new orchestrator coroutine
  |-- Orchestrator resumes at Phase 5: apply decisions
  |-- Continues through Phase 6-7 to completion
  |-- Cleans up paused state
```

State is serialized to Redis hash so the orchestrator can resume after container restarts. On startup, worker checks `agent:paused` for any interrupted tasks.

## Nexar API: Verified Behavior (Live-Tested April 2026)

### Subscription Limitations (Current Plan)

Fields tested and confirmed **UNAUTHORIZED**:
- `specs` — requires TECH_SPECS add-on ("Please upgrade your Nexar subscription")
- `bestDatasheet` — requires DATASHEETS add-on
- `bestImage` — requires DATASHEETS add-on
- `similarParts` — requires ENTERPRISE plan

Fields confirmed **AVAILABLE**:
- `mpn`, `manufacturer { name }`, `shortDescription`
- `medianPrice1000 { price currency }`
- `totalAvail` (global stock count)
- `sellers { company { name } isAuthorized offers { inventoryLevel moq sku prices { quantity price currency convertedPrice convertedCurrency } clickUrl } }`
- `category { name path }`
- `octopartUrl` (direct link to Octopart listing)
- `sellers(authorizedOnly: true)` filter works

### Query Types Tested

| Query | Best For | Parts/Query |
|-------|----------|-------------|
| `supSearch(q, limit)` | Keyword/description search ("10kohm resistor 0603") | limit (default 10) |
| `supSearchMpn(q, limit)` | MPN lookup ("OPA1612AIDR") — better matching than supSearch | limit (default 10) |
| `supMultiMatch(queries)` | Batch MPN lookup — single query for N MPNs | 3 per MPN (default) |

### Key Findings

1. **`supSearch` for MPN is unreliable** — searching "LM386" returns "LM386" by Universal Microelectronics (0 stock) instead of "LM386N-1/NOPB" by TI. Use `supSearchMpn` for MPN lookups.

2. **`supMultiMatch` is significantly more quota-efficient** — returns default 3 parts per MPN vs 5-10 for individual queries. One HTTP request for all MPNs.

3. **`country` and `currency` parameters work** — `country: "PL"` returns Polish distributors (Farnell, TME) with PLN prices. `currency: "EUR"` adds `convertedPrice`/`convertedCurrency` fields.

4. **`totalAvail` gives instant global stock** — no need to sum individual seller inventories.

5. **`shortDescription` is the only spec data available** — must rely on LLM knowledge + description to verify component constraints. Example: "Operational Amplifier, 2 Func, 500uV Offset-Max, BIPolar, PDSO8"

6. **Current code bugs found:**
   - `nexar_client.py` requests `specs` and `bestDatasheet` in SEARCH_QUERY but they always return `None` (unauthorized)
   - `_compress_part()` builds empty specs array and null datasheet_url
   - `check_lifecycle()` always returns "unknown" because lifecycle comes from `specs`
   - `multi_match()` is a sequential loop of individual searches instead of using native `supMultiMatch`
   - Both `search_parts()` and `search_mpn()` use the same `supSearch` query

### Required mcp-nexar Changes

1. **Replace `SEARCH_QUERY`** — remove `specs`, `bestDatasheet`. Add `totalAvail`, `category { name }`, `octopartUrl`, `sellers(authorizedOnly: true)`, `moq`, `sku`
2. **Add `supSearchMpn` query** — use for `search_mpn()` method
3. **Add native `supMultiMatch` query** — replace sequential loop in `multi_match()`
4. **Add `country`/`currency` parameters** — pass through from environment or tool args
5. **Remove `check_lifecycle` tool** — always returns "unknown" on current plan
6. **Remove `KEY_SPECS` filtering** — specs are never available
7. **Update `_compress_part()`** — include `totalAvail`, `octopartUrl`, `category`, remove dead `specs`/`datasheet_url` code

## MCP Server Changes

### Keep as-is
- **mcp-documents** (:8003) — PDF rendering, image processing, text extraction
- **mcp-export** (:8005) — CSV/KiCad/Altium generation

### Fix (mcp-nexar)
- **mcp-nexar** (:8001) — Requires significant GraphQL query updates (see "Required mcp-nexar Changes" above). New queries for `supSearchMpn` and `supMultiMatch`. Remove dead code for unauthorized fields. Add country/currency support.

### Simplify
- **mcp-snapmagic** (:8002) — Currently uses Tavily API (not LiteLLM) for web search against snapeda.com. Remove the unused `LITELLM_BASE_URL` env var. Keep Tavily as the search mechanism. Parallelize `check_cad_batch` internally.
- **mcp-websearch** (:8004) — Currently uses LiteLLM proxy with `gpt-4o-mini` and a `web_search` tool type. Must be updated to use OpenAI API directly with `gpt-4o-mini` and the `web_search_preview` tool (OpenAI's native web search). Gets its own `OPENAI_API_KEY` env var.

### Remove
- **litellm-proxy** (:4000) — No longer needed. Agent talks to OpenAI directly.

## File Structure

```
agent/
  main.py                  # Entry point, signal handling (minimal changes)
  worker.py                # Redis queue consumer (simplified, no post-processing)
  orchestrator.py          # Phase-based orchestration (new)
  search_agent.py          # Focused search sub-agent with tool loop (new)
  llm_client.py            # Thin AsyncOpenAI wrapper, direct to OpenAI (new)
  mcp_router.py            # Cleaned up tool-to-server routing
  state.py                 # Task state serialization for pause/resume (new)
  prompts/
    orchestrator.py        # System prompt for schematic analysis
    search_agent.py        # System prompt for component search
  models.py                # Pydantic models: ComponentSpec, SearchResult, etc. (new)
  Dockerfile               # Updated dependencies
  requirements.txt         # Add pydantic, keep openai/redis/httpx/Pillow
  tests/
    conftest.py
    test_orchestrator.py
    test_search_agent.py
    test_state.py
    test_worker.py
```

### Deleted files
- `agent_runner.py` (822 lines) — replaced by orchestrator.py + search_agent.py
- `prompts.py` (393 lines) — replaced by prompts/orchestrator.py + prompts/search_agent.py

### Simplified files
- `worker.py` — remove post-processing (_fix_sourcing_issues, _generate_exports, _prepare_attachments). These move to orchestrator phases.

## Backend Changes

### websocket/manager.py
- Handle `decision_required` message type from agent pub/sub
- Forward to connected WebSocket clients
- Add phase-aware status messages ("Analyzing schematic...", "Searching 15 components...", "Waiting for your decision...")

### routers/messages.py
- Detect `decision_id` field in incoming messages
- Route decision responses to `agent:decisions:{task_id}` via Redis
- Regular messages still go through normal task submission

### task_manager.py
- Add `submit_decision(task_id, decision)` method
- Pushes decision to Redis list instead of creating new task

### routers/messages.py — _generate_title()
- The existing `_generate_title()` function calls LiteLLM proxy to generate conversation titles via `gpt-4o-mini`
- Must be updated to use a direct OpenAI client (`httpx` POST to `api.openai.com/v1/chat/completions`) with the backend's own `OPENAI_API_KEY`
- Backend `.env` gets `OPENAI_API_KEY` added (same key as agent uses)

## Frontend Changes

### components/MessageBubble.tsx
- Render `decision_required` messages as interactive cards
- Show question text + clickable option buttons
- On click, call sendDecision() and disable buttons
- Show selected option after choice

### lib/api.ts
- Add `sendDecision(conversationId, decisionId, choice)` function
- POST to existing messages endpoint with decision payload

### hooks/useWebSocket.ts
- Handle `decision_required` message type (pass through to state)

### components/StatusIndicator.tsx
- Phase-aware status display with descriptive messages per phase

## Docker Changes

### docker-compose.yml
- Remove `litellm-proxy` service entirely
- Update `agent` service: remove LITELLM_BASE_URL, add OPENAI_MODEL=gpt-5.4, add OPENAI_REASONING_EFFORT=high, remove depends_on litellm-proxy
- Update `backend` service: add OPENAI_API_KEY (for _generate_title)
- Update `mcp-websearch`: replace LITELLM_BASE_URL with own OPENAI_API_KEY
- Update `mcp-snapmagic`: remove unused LITELLM_BASE_URL env var

### .env.example
- Remove: LITELLM_BASE_URL
- Add: OPENAI_MODEL=gpt-5.4

Result: 13 containers -> 12 containers.

Note: Update CLAUDE.md to reflect the new container count and architecture changes.

## Response Format

The agent produces four message types (up from three). All are persisted to Supabase and rendered by the frontend.

### Existing types (unchanged)
- **`needs_clarification`** — agent needs more info from the user before proceeding
- **`recommendation`** — final BOM with components, pricing, stock, exports
- **`analysis`** — intermediate analysis or status update

### New type
- **`decision_required`** — agent is paused, waiting for user to choose between options

Schema for `decision_required` messages persisted in Supabase:
```json
{
  "status": "decision_required",
  "task_id": "abc-123",
  "message": "Some components need your input before I can finalize the BOM.",
  "decisions": [
    {
      "decision_id": "d1",
      "ref": "U3",
      "mpn": "LM386N",
      "issue": "no_cad_model",
      "question": "LM386N has no 3D model on SnapMagic",
      "options": [
        {"key": "A", "label": "Add without 3D model"},
        {"key": "B", "label": "Use LM386M-1 (has model, $0.15 more)", "mpn": "LM386M-1"}
      ],
      "resolved": false,
      "chosen": null
    }
  ]
}
```

When the user responds, the Supabase record is updated: `resolved: true, chosen: "B"`. This ensures decision cards render correctly when loaded from history (not just from WebSocket).

## Testing Strategy

### Unit tests (mocked MCP + LLM)
- **test_orchestrator.py** — mock MCPRouter and LLM client. Test phase sequencing, state serialization/deserialization, pause/resume logic, timeout auto-resolution.
- **test_search_agent.py** — mock MCPRouter. Test tool loop with canned Nexar responses, retry on no results, lifecycle rejection, error handling.
- **test_state.py** — mock Redis. Test serialization roundtrip, resume from saved state, cleanup after completion.
- **test_worker.py** — mock Redis. Test task consumption, decision listener, orphan recovery, paused task recovery on startup.

### Integration tests
- **test_integration.py** — requires running Redis (use testcontainers or docker). Test full pause/resume cycle: submit task → orchestrator pauses → push decision to Redis → orchestrator resumes → task completes.

### Fixtures (conftest.py)
- `mock_llm_client` — returns canned OpenAI responses (component list JSON, search results)
- `mock_mcp_router` — returns canned tool results per tool name
- `mock_redis` — in-memory Redis mock for queue/pubsub/hash operations

## Redis Keys

Existing (unchanged):
- `agent:tasks` — input task queue
- `agent:processing` — in-flight tasks
- `agent:completed` — completed tasks
- `agent:failed` — failed tasks
- `agent:status:{conversation_id}` — pub/sub for status updates

New:
- `agent:paused` — list of task IDs currently waiting for user decisions
- `agent:task_state:{task_id}` — Redis hash storing serialized orchestrator state for pause/resume
- `agent:decisions:{task_id}` — Redis list for user decision responses (LPUSH by backend, popped by worker)
