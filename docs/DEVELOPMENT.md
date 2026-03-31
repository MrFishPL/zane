# Development guide

## Adding a new MCP tool

MCP servers are in directories named `mcp-*`. Each is an independent Python service using the `FastMCP` framework.

### 1. Add the tool function

Open the relevant `server.py` file (e.g., `mcp-nexar/server.py`) and add a new function decorated with `@mcp.tool()`:

```python
@mcp.tool()
async def my_new_tool(param1: str, param2: int) -> dict:
    """Tool description shown to the agent.

    Args:
        param1: Description of param1.
        param2: Description of param2.

    Returns:
        Dict with result fields.
    """
    start = time.monotonic()
    try:
        result = do_work(param1, param2)
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("my_new_tool", f"param1={param1}", duration_ms, True)
        return result
    except Exception as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        _log_tool_call("my_new_tool", f"param1={param1}", duration_ms, False, str(exc))
        return {"error": str(exc)}
```

Key conventions:
- Always return a `dict`, never raise exceptions to the agent.
- Log every tool call with `_log_tool_call()` (tool name, params truncated to 200 chars, duration, success/error).
- Use `time.monotonic()` for timing.

### 2. Add a new MCP server

To create an entirely new MCP server:

1. Create a directory `mcp-myservice/` with `server.py`, `Dockerfile`, and `requirements.txt`.
2. Follow the pattern from existing servers (e.g., `mcp-nexar/server.py`).
3. Include a `/health` endpoint:
   ```python
   @mcp.custom_route("/health", methods=["GET"])
   async def health(request: Request) -> JSONResponse:
       return JSONResponse({"status": "ok", "service": "mcp-myservice"})
   ```
4. Add the service to `docker-compose.yml` with a health check, port mapping, and network membership.
5. Add it to the agent container's `depends_on` list.

### 3. Write tests

Add a `tests/` directory with pytest tests. Mock external API calls. See existing tests in `mcp-nexar/tests/`, `mcp-documents/tests/`, etc.

## Modifying agent behavior

The agent is defined via markdown files using the Anthropic Agent SDK. Changing agent behavior means editing text files -- no code changes required.

### Agent file structure

```
agent/
  CLAUDE.md                          # Main system prompt and identity
  .claude/
    agents/                          # Subagent definitions (YAML frontmatter + markdown)
      schematic-analyzer.md          # Analyzes schematic images
      component-sourcer.md           # Searches Nexar, compares prices
      cad-checker.md                 # Checks SnapMagic availability
      export-generator.md            # Generates CSV and library files
    rules/                           # Domain rules
      component-selection.md         # Component selection criteria
      data-sources.md                # Data source priorities
      output-format.md               # JSON output format spec
      error-handling.md              # Error handling procedures
```

### Common modifications

- **Change selection priority**: edit `agent/.claude/rules/component-selection.md`.
- **Change output format**: edit `agent/.claude/rules/output-format.md`.
- **Add a new subagent**: create a new markdown file in `agent/.claude/agents/` with YAML frontmatter defining the subagent's tools, prompt, and delegation rules.
- **Change the main system prompt**: edit `agent/CLAUDE.md`.

### Switching LLM models

To switch from GPT-5.4 to Claude Sonnet, change one line in `litellm-proxy/litellm_config.yaml`:

```yaml
model_list:
  - model_name: gpt-5.4
    litellm_params:
      model: anthropic/claude-sonnet-4-6   # was: openai/gpt-5.4
      api_key: os.environ/ANTHROPIC_API_KEY
```

No changes to agents or MCP servers are needed.

## Running tests

Each service has its own test suite in a `tests/` directory.

### Python services (pytest)

```bash
# Backend
cd backend && pytest

# MCP servers
cd mcp-nexar && pytest
cd mcp-documents && pytest
cd mcp-export && pytest
cd mcp-websearch && pytest
cd mcp-snapmagic && pytest

# Agent
cd agent && pytest
```

Run live/smoke tests (requires real API credentials):

```bash
cd mcp-nexar && pytest -m live
```

### Frontend (npm)

```bash
cd frontend && npm test
```

### Running tests inside Docker

```bash
docker compose exec backend pytest
docker compose exec mcp-nexar pytest
docker compose exec frontend npm test
```

## Viewing logs

### Grafana

Open [http://localhost:3001](http://localhost:3001) (default login: `admin`/`admin`).

Grafana is pre-provisioned with Loki as a data source. All container logs are shipped to Loki via the Docker Loki logging driver.

### Useful LogQL queries

**All errors across services:**
```logql
{compose_service=~".+"} |= "error"
```

**Agent task lifecycle:**
```logql
{compose_service="backend"} | json | msg="task_manager.*"
```

**MCP tool calls for a specific service:**
```logql
{compose_service="mcp-nexar"} | json | msg="tool_call"
```

**Slow tool calls (over 5 seconds):**
```logql
{compose_service=~"mcp-.*"} | json | duration_ms > 5000
```

**HTTP requests to backend:**
```logql
{compose_service="backend"} | json | msg="http.request"
```

**Failed agent tasks:**
```logql
{compose_service="backend"} | json | msg="task_manager.task.failed"
```

**Filter by conversation ID:**
```logql
{compose_service=~".+"} | json | conversation_id="<uuid>"
```

**WebSocket connection events:**
```logql
{compose_service="backend"} | json | msg=~"ws\\..*"
```

### Docker logs (without Grafana)

```bash
docker compose logs backend --follow
docker compose logs mcp-nexar --follow --tail 100
docker compose logs agent --follow
```

## Code structure

```
zane/
  docker-compose.yml           # All 13 services
  .env.example                 # Environment variable template
  backend/
    main.py                    # FastAPI app entry point
    routers/
      conversations.py         # Conversation CRUD endpoints
      messages.py              # Message creation + agent task dispatch
      upload.py                # File upload endpoint
      files.py                 # File download/serving endpoint
    services/
      supabase_client.py       # Supabase CRUD operations
      minio_client.py          # MinIO file operations
      redis_client.py          # Redis queue and pub/sub
      task_manager.py          # Agent task lifecycle management
    websocket/
      manager.py               # WebSocket connection + Redis bridge
    tests/
  frontend/
    src/                       # Next.js App Router
  agent/                       # Anthropic Agent SDK worker
  mcp-nexar/                   # Nexar component search
  mcp-snapmagic/               # CAD model availability
  mcp-documents/               # PDF/image processing
  mcp-websearch/               # Web search fallback
  mcp-export/                  # BOM export generation
  litellm-proxy/               # LiteLLM proxy config + Dockerfile
  observability/               # Loki + Grafana config
  redis/                       # Redis config
  supabase/                    # Supabase schema
  spec/                        # Project specification + Mermaid diagrams
```
