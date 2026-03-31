# Zane - Electronics Component Sourcing Agent

Zane is a web application for automated electronic component sourcing. Upload a schematic (PDF, photo, or hand-drawn sketch), describe your requirements in natural language, and an AI agent visually analyzes the schematic, identifies components, searches distributor APIs for real purchasable parts, and produces a structured BOM with pricing, stock levels, distributor links, and downloadable CSV/library files for KiCad and Altium Designer.

## Architecture

Zane runs as 13 Docker containers on a single network. The frontend (Next.js) communicates with a FastAPI backend, which dispatches agent tasks via Redis to an Anthropic Agent SDK worker. The agent orchestrates five MCP tool servers for component search (Nexar), CAD model checks (SnapMagic), document processing, web search fallback, and BOM export generation.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture description, container map, and data flow diagrams.

## Quick start

```bash
git clone <repo-url> && cd zane
cp .env.example .env
# Fill in your API keys in .env (see docs/SETUP.md for details)
docker compose up
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, containers, communication patterns |
| [docs/SETUP.md](docs/SETUP.md) | Prerequisites, environment variables, first run guide |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Adding MCP tools, modifying agents, running tests, viewing logs |
| [docs/API.md](docs/API.md) | REST endpoints, WebSocket protocol, error format |
| [docs/AGENT.md](docs/AGENT.md) | Agent behavior, subagents, response format, vision flow |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues, debugging tips, log queries |

## External services

- **Supabase** -- hosted PostgreSQL database
- **OpenAI API** -- GPT-5.4 model (accessed through LiteLLM proxy)
- **Nexar API** -- electronic component search (Octopart GraphQL API)
