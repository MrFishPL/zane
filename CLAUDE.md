# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Web application for automated electronic component sourcing. User uploads a schematic (PDF/photo/sketch), AI agent visually analyzes it, identifies components, searches TME distributor API, and produces a structured BOM with pricing, stock, and downloadable CSV/KiCad/Altium exports.

## Architecture

11 Docker containers on a single network (`app-network`):

- **frontend** (Next.js :3000) — Chat UI with file upload, WebSocket status, BOM rendering
- **backend** (FastAPI :8000) — REST API, Supabase CRUD, MinIO files, Redis pub/sub → WebSocket
- **agent** (custom Orchestrator worker) — Picks tasks from Redis queue, runs a 6-phase pipeline (parse → analyze → search → assemble BOM → export). Uses LLMClient (Anthropic API, Claude), SearchAgent sub-agent with tool loop, StateManager for Redis pause/resume.
- **redis** (Redis 7 :6379) — Task queue (`agent:tasks`) + pub/sub (`agent:status:{conv_id}`)
- **minio** (MinIO :9000/:9001) — Object storage for uploads/temp/exports
- **mcp-tme** (:8001) — TME electronic component search (REST, HMAC-SHA1)
- **mcp-documents** (:8003) — PDF rendering, image processing, base64 retrieval (MinIO)
- **mcp-websearch** (:8004) — Web search fallback (Anthropic API with web_search tool)
- **mcp-export** (:8005) — CSV/KiCad/Altium library generation (MinIO)
- **loki** (:3100) + **grafana** (:3001) — Observability

External: Supabase (PostgreSQL), Anthropic API (Claude), TME API

## Environment

All credentials in `.env` (gitignored). See `.env.example` for template.

Key vars: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `TME_APP_TOKEN`, `TME_APP_SECRET`, `TME_LANGUAGE`, `TME_COUNTRY`, `SUPABASE_URL`, `SUPABASE_KEY`, `REDIS_URL`, `MINIO_ENDPOINT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`

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

## TME API Notes

- Authentication: HMAC-SHA1 signature (OAuth 1.0a style) with app token + app secret
- Base URL: `https://api.tme.eu/`
- All endpoints use POST with `application/x-www-form-urlencoded`
- Key endpoints: `Products/Search`, `Products/GetProducts`, `Products/GetPricesAndStocks`
- Rate limits: 10 req/s standard, 2 req/s for price/stock endpoints
- Max 50 symbols per batch request

## Key Patterns

- **Backend ↔ Agent**: Redis queue (BLMOVE/LREM) + pub/sub — no direct HTTP
- **Agent ↔ Tools**: All through MCP servers (HTTP+SSE transport), never direct code execution
- **MCP servers**: Python `mcp` SDK with `FastMCP`, each exposes `/health` + `/mcp` (SSE)
- **Structured logging**: All Python services use `structlog` → JSON to stdout → Loki
- **Agent responses**: Always JSON with `status` field: `needs_clarification`, `recommendation`, or `analysis`
- **MinIO paths**: `uploads/{user_id}/{conversation_id}/`, `temp/...`, `exports/...`
- **Shared user ID**: `00000000-0000-0000-0000-000000000001` (no auth for now)
