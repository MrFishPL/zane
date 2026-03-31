# Troubleshooting

## Common issues

### Services fail to start

**Symptom**: `docker compose up` fails with dependency errors.

**Cause**: services depend on each other via health checks. If a health check fails, dependent services will not start.

**Fix**:
1. Check which service is unhealthy: `docker compose ps`
2. Check that service's logs: `docker compose logs <service-name>`
3. Common causes:
   - Missing environment variables (check `.env` file)
   - Port conflicts (another process using 3000, 8000, 6379, etc.)
   - Docker Loki logging driver not installed (install with `docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions`, or remove the `logging:` sections from `docker-compose.yml`)

### Backend cannot connect to Supabase

**Symptom**: backend logs show `supabase` connection errors.

**Fix**: verify `SUPABASE_URL` and `SUPABASE_KEY` in `.env`. The key should be the service role key (starts with `eyJ`), not the anon key. Ensure the database schema has been created (see [SETUP.md](SETUP.md#supabase-database-schema)).

### mcp-nexar authentication failures

**Symptom**: `search_parts` tool returns `{"error": "authentication failed"}`.

**Fix**: verify `NEXAR_CLIENT_ID` and `NEXAR_CLIENT_SECRET` in `.env`. These are OAuth2 credentials from [nexar.com](https://nexar.com). The server authenticates using client credentials flow against `https://identity.nexar.com/connect/token`.

### Agent task stuck in "running" state

**Symptom**: conversation shows spinner indefinitely, agent never completes.

**Possible causes**:
1. Agent container crashed during processing. Check: `docker compose logs agent`
2. Redis connection lost. Check: `docker compose exec redis redis-cli ping`
3. LiteLLM proxy down or OpenAI API errors. Check: `docker compose logs litellm-proxy`
4. MCP server unreachable. Check health endpoints for all MCP servers.

**Recovery**: restart the agent container. On startup, it checks the `agent:processing` Redis list for orphaned tasks and requeues them.

```bash
docker compose restart agent
```

### File upload fails with 415 error

**Symptom**: upload returns "Unsupported file type".

**Fix**: only these MIME types are allowed: `application/pdf`, `image/png`, `image/jpeg`, `image/webp`. Ensure the file has the correct extension and your browser is sending the right Content-Type.

### WebSocket disconnects immediately

**Symptom**: frontend shows "disconnected" status, cannot receive agent updates.

**Possible causes**:
1. CORS issue -- the backend only allows WebSocket connections from `http://localhost:3000`. If the frontend is on a different origin, update `allow_origins` in `backend/main.py`.
2. Proxy/load balancer stripping WebSocket headers. If running behind a reverse proxy, ensure it supports WebSocket upgrade.

### MinIO bucket errors

**Symptom**: upload or download fails with bucket-not-found errors.

**Fix**: the backend auto-creates buckets (`uploads`, `temp`, `exports`) on startup. If buckets are missing, restart the backend:

```bash
docker compose restart backend
```

Or create them manually via the MinIO console at [http://localhost:9001](http://localhost:9001) (login: `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from `.env`).

### Grafana shows no logs

**Symptom**: Grafana dashboard is empty, no log data in Loki.

**Fix**:
1. Verify the Docker Loki logging driver is installed: `docker plugin ls`
2. Verify Loki is healthy: `curl http://localhost:3100/ready`
3. Check that Grafana's Loki datasource is configured correctly (should be pre-provisioned)
4. If using Docker Desktop, the Loki driver may need the host URL instead of container name. Check `loki-url` in the Docker Compose logging config.

### Agent returns empty or unexpected responses

**Symptom**: agent response is missing components or has wrong format.

**Check**:
1. LiteLLM proxy logs for model errors: `docker compose logs litellm-proxy`
2. Agent logs for tool call failures: `docker compose logs agent`
3. MCP server logs for individual tool errors (see log queries below)

### Port conflicts

**Default ports used**:
| Port | Service |
|------|---------|
| 3000 | frontend |
| 3001 | grafana |
| 3100 | loki |
| 4000 | litellm-proxy |
| 6379 | redis |
| 8000 | backend |
| 8001 | mcp-nexar |
| 8002 | mcp-snapmagic |
| 8003 | mcp-documents |
| 8004 | mcp-websearch |
| 8005 | mcp-export |
| 9000 | minio API |
| 9001 | minio console |

If any port is in use, either stop the conflicting process or change the port mapping in `docker-compose.yml`.

## Debugging tips

### Inspect Redis queue state

```bash
# Check task queue length
docker compose exec redis redis-cli LLEN agent:tasks

# View pending tasks (without removing them)
docker compose exec redis redis-cli LRANGE agent:tasks 0 -1

# Check processing list (orphaned tasks)
docker compose exec redis redis-cli LRANGE agent:processing 0 -1

# Monitor all Redis pub/sub messages in real time
docker compose exec redis redis-cli PSUBSCRIBE "agent:status:*"
```

### Inspect MinIO contents

Open the MinIO console at [http://localhost:9001](http://localhost:9001) and browse the `uploads`, `temp`, and `exports` buckets.

Or use the MinIO CLI:
```bash
docker compose exec minio mc ls local/uploads/ --recursive
docker compose exec minio mc ls local/exports/ --recursive
```

### Test MCP servers directly

Each MCP server has a `/health` endpoint:
```bash
curl http://localhost:8001/health   # mcp-nexar
curl http://localhost:8002/health   # mcp-snapmagic
curl http://localhost:8003/health   # mcp-documents
curl http://localhost:8004/health   # mcp-websearch
curl http://localhost:8005/health   # mcp-export
```

### Check Supabase data

Use the Supabase dashboard to inspect tables directly:
- `conversations` -- all conversations
- `messages` -- message history
- `agent_tasks` -- task status, errors, current_status text

### View structured logs

All Python services output structured JSON logs via structlog. Key fields:

| Field | Description |
|-------|-------------|
| `timestamp` | ISO 8601 timestamp |
| `level` | Log level (info, error, warning) |
| `event` / `msg` | Log event name |
| `conversation_id` | Conversation UUID (when applicable) |
| `task_id` | Agent task UUID (when applicable) |
| `tool` | MCP tool name (in tool call logs) |
| `duration_ms` | Operation duration in milliseconds |
| `success` | Whether the tool call succeeded |
| `error` | Error message (when applicable) |

## Log queries (LogQL for Grafana)

### Service-specific logs

```logql
# All backend logs
{compose_service="backend"}

# All agent logs
{compose_service="agent"}

# Specific MCP server
{compose_service="mcp-nexar"}
```

### Error investigation

```logql
# All errors across all services
{compose_service=~".+"} |= "error"

# Failed agent tasks
{compose_service="backend"} | json | msg="task_manager.task.failed"

# MCP tool errors
{compose_service=~"mcp-.*"} | json | success=false

# Supabase errors
{compose_service="backend"} | json | msg=~"supabase\\..*\\.error"
```

### Performance

```logql
# Slow HTTP requests (over 2 seconds)
{compose_service="backend"} | json | msg="http.request" | duration_ms > 2000

# Slow MCP tool calls (over 5 seconds)
{compose_service=~"mcp-.*"} | json | msg="tool_call" | duration_ms > 5000

# Agent task durations
{compose_service="backend"} | json | msg="task_manager.task.completed"
```

### Tracking a specific conversation

```logql
# All logs for a conversation across all services
{compose_service=~".+"} | json | conversation_id="<paste-uuid-here>"
```

### Upload and file operations

```logql
# File uploads
{compose_service="backend"} | json | msg="upload.success"

# MinIO operations
{compose_service="backend"} | json | msg=~"minio\\..*"

# Staging cleanup
{compose_service="backend"} | json | msg="staging_cleanup.run"
```

### WebSocket events

```logql
# WebSocket connections and disconnections
{compose_service="backend"} | json | msg=~"ws\\..*"

# Redis listener events
{compose_service="backend"} | json | msg=~"ws\\.redis_listener.*"
```
