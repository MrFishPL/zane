# Setup guide

## Prerequisites

- **Docker** (v20.10+)
- **Docker Compose** (v2.0+ -- included with Docker Desktop)
- **Docker Loki logging driver** (optional, for log aggregation):
  ```bash
  docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions
  ```

## Step-by-step setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd zane
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in the required API credentials. See the environment variables table below for details.

### 3. Start all services

```bash
docker compose up
```

This builds and starts all 13 containers. On first run, the backend automatically creates the required MinIO buckets (`uploads`, `temp`, `exports`).

### 4. Verify health

Check that all services are running:

```bash
docker compose ps
```

Verify individual service health:

```bash
# Backend
curl http://localhost:8000/health

# MCP servers
curl http://localhost:8001/health   # mcp-nexar
curl http://localhost:8002/health   # mcp-snapmagic
curl http://localhost:8003/health   # mcp-documents
curl http://localhost:8004/health   # mcp-websearch
curl http://localhost:8005/health   # mcp-export

# LiteLLM proxy
curl http://localhost:4000/health

# Redis
docker compose exec redis redis-cli ping
```

Open the application at [http://localhost:3000](http://localhost:3000).

## Environment variables

| Variable | Required | Used by | Example | Description |
|----------|----------|---------|---------|-------------|
| `OPENAI_API_KEY` | Yes | litellm-proxy | `sk-...` | OpenAI API key for GPT-5.4 and GPT-4o-mini |
| `NEXAR_CLIENT_ID` | Yes | mcp-nexar | (from nexar.com) | Nexar OAuth2 client ID for component search |
| `NEXAR_CLIENT_SECRET` | Yes | mcp-nexar | (from nexar.com) | Nexar OAuth2 client secret |
| `SUPABASE_URL` | Yes | backend | `https://xxx.supabase.co` | Supabase project URL |
| `SUPABASE_KEY` | Yes | backend | `eyJ...` | Supabase service role key |
| `REDIS_URL` | No | backend, agent | `redis://redis:6379/0` | Redis connection URL (default works for Docker) |
| `LITELLM_BASE_URL` | No | agent, backend, mcp-snapmagic, mcp-websearch | `http://litellm-proxy:4000` | LiteLLM proxy endpoint (default works for Docker) |
| `MINIO_ENDPOINT` | No | backend, mcp-documents, mcp-export | `minio:9000` | MinIO API endpoint (default works for Docker) |
| `MINIO_ROOT_USER` | No | minio, backend, mcp-documents, mcp-export | `minioadmin` | MinIO access key (change for production) |
| `MINIO_ROOT_PASSWORD` | No | minio, backend, mcp-documents, mcp-export | `minioadmin` | MinIO secret key (change for production) |
| `LOKI_URL` | No | all (Docker logging driver) | `http://loki:3100` | Loki endpoint for log shipping |
| `AGENT_MAX_CONCURRENT_TASKS` | No | agent | `50` | Max concurrent agent tasks (default: 50) |

Variables marked "No" for Required have sensible defaults in `.env.example` that work for local Docker development.

## CORS configuration

The backend allows CORS requests from `http://localhost:3000` (the frontend). If you change the frontend port or deploy to a different origin, update the `allow_origins` list in `backend/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Update this
    ...
)
```

## MinIO bucket auto-creation

On startup, the backend automatically creates the following MinIO buckets if they do not exist:

- `uploads` -- user-uploaded files (PDFs, images)
- `temp` -- temporary files (rendered PDF pages, annotated images, crops)
- `exports` -- downloadable files (CSV BOMs, KiCad/Altium library ZIPs)

No manual bucket configuration is needed.

## Supabase database schema

The Supabase database must have the following tables created before first use:

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

CREATE TABLE agent_tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
  message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
  current_status TEXT,
  error TEXT,
  started_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE library_profiles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  workspace_url TEXT,
  preferences JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Default shared user
INSERT INTO users (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'shared')
ON CONFLICT DO NOTHING;
```

## Stopping the application

```bash
docker compose down          # Stop containers
docker compose down -v       # Stop containers and remove volumes (deletes all data)
```
