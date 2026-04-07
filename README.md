# Zane - AI Electronics Component Sourcing

Upload a schematic, describe what you need in natural language, and Zane finds real, purchasable components on TME with pricing and stock levels.

![demo](demo.jpeg)

## What it does

You upload a schematic (PDF, photo, or hand-drawn sketch) and describe your requirements. Zane's AI agent:

1. **Analyzes** the schematic using computer vision (Claude)
2. **Identifies** all electronic components, values, packages, and constraints
3. **Searches** TME distributor API for real parts matching each spec
4. **Produces** a structured BOM with pricing, stock, distributor links
5. **Exports** downloadable CSV files for KiCad and Altium Designer

### Demo prompt

> Przygotuj liste elementow do miksera audio na podstawie dolaczonego pliku. Zastap tranzystory BC549 jakimis zamiennikami SMD, tak samo zastap uklad TDA2320 czyms tanszym. Rezystory maja miec rozmiar 0603, uzyj kondensatorow uznanych firm. Rozmiar bez znaczenia, ale do toru audio uzyj takich, ktorych dielektryk sie do tego nadaje. Kondensatory dobrze by bylo zeby byly SMD, ale nie musza byc. Priorytetyzuj niska cene przy 1000 sztuk calego urzadzenia. Liste komponentow dostosuj tak, aby mikser mial 8 kanalow. Znajdz jakies fajne gniazda jack 6,3mm do druku na wejscia i wyjscia. Potencjometry maja byc obrotowe, trwale i odporne na kurz.

The agent understands complex, multi-constraint requirements in any language, searches TME for each component, and returns a complete BOM optimized for your production volume.

---

## Architecture

### Microservices (Docker containers)

```mermaid
graph TB
    subgraph External
        Supabase[(Supabase<br/>PostgreSQL)]
        Anthropic[Anthropic API<br/>Claude]
        TME[TME API<br/>tme.eu]
    end

    subgraph Docker Network
        subgraph Core
            FE[Frontend<br/>Next.js :3000]
            BE[Backend<br/>FastAPI :8000]
            AG[Agent<br/>Orchestrator]
        end

        subgraph Infrastructure
            RD[(Redis 7<br/>:6379)]
            MO[(MinIO<br/>:9000)]
        end

        subgraph MCP Servers
            MTME[mcp-tme<br/>:8001]
            MDOC[mcp-documents<br/>:8003]
            MWS[mcp-websearch<br/>:8004]
            MEXP[mcp-export<br/>:8005]
        end

        subgraph Observability
            LK[Loki :3100]
            GF[Grafana :3001]
        end
    end

    FE -->|REST + WebSocket| BE
    BE -->|CRUD| Supabase
    BE -->|Files| MO
    BE -->|Task Queue + Pub/Sub| RD
    AG -->|BLMOVE / Pub/Sub| RD
    AG -->|SSE/HTTP| MTME
    AG -->|SSE/HTTP| MDOC
    AG -->|SSE/HTTP| MWS
    AG -->|SSE/HTTP| MEXP
    AG -->|LLM calls| Anthropic
    MTME -->|HMAC-SHA1| TME
    MDOC -->|Read/Write| MO
    MEXP -->|Write| MO
    MWS -->|web_search| Anthropic
    GF -->|Query| LK

    style FE fill:#1a1a2e,stroke:#4a9eff
    style BE fill:#1a1a2e,stroke:#4a9eff
    style AG fill:#1a1a2e,stroke:#e74c3c
    style RD fill:#1a1a2e,stroke:#d63031
    style MO fill:#1a1a2e,stroke:#00b894
```

### Agent workflow

```mermaid
flowchart TD
    START([User sends message<br/>+ schematic]) --> P1

    subgraph Phase 1: Parse
        P1[Parse attachments<br/>PDF rendering, image extraction]
    end

    P1 --> P2

    subgraph Phase 2: Analyze
        P2[LLM analyzes schematic<br/>Vision + extracted text]
        P2 --> COMP[Components identified<br/>with values, packages, constraints]
    end

    COMP --> P3

    subgraph Phase 3: Search
        P3[Batch pre-search<br/>known MPNs via multi_match]
        P3 --> PAR[Parallel sub-agent search<br/>max 5 concurrent]
        PAR --> SA1[SearchAgent<br/>Component 1]
        PAR --> SA2[SearchAgent<br/>Component 2]
        PAR --> SAN[SearchAgent<br/>Component N]
    end

    SA1 & SA2 & SAN --> P6

    subgraph Phase 6: Assemble
        P6[Merge search results<br/>into BOM entries]
    end

    P6 --> P7

    subgraph Phase 7: Export
        P7[Generate CSV<br/>KiCad + Altium files]
    end

    P7 --> RESULT([Return recommendation<br/>with BOM + export links])

    style START fill:#4a9eff,color:#fff
    style RESULT fill:#00b894,color:#fff
```

### SearchAgent ReAct loop

```mermaid
flowchart LR
    THINK[Think<br/>Plan strategy] --> ACT[Act<br/>Search TME]
    ACT --> OBSERVE[Observe<br/>Check results]
    OBSERVE --> REFLECT{Match<br/>found?}
    REFLECT -->|No| THINK
    REFLECT -->|Yes| SUBMIT[Submit<br/>result]
    REFLECT -->|Budget<br/>exhausted| SUBMIT

    style THINK fill:#f39c12,color:#fff
    style ACT fill:#3498db,color:#fff
    style OBSERVE fill:#9b59b6,color:#fff
    style SUBMIT fill:#00b894,color:#fff
```

### Task flow (Backend <-> Agent)

```mermaid
sequenceDiagram
    participant U as Browser
    participant FE as Frontend
    participant BE as Backend
    participant RD as Redis
    participant AG as Agent
    participant MCP as MCP Servers

    U->>FE: Upload schematic + message
    FE->>BE: POST /api/{conv}/messages
    BE->>RD: LPUSH agent:tasks
    BE-->>FE: 202 Accepted

    FE->>BE: WebSocket connect
    BE->>RD: SUBSCRIBE agent:status:{conv}

    RD->>AG: BLMOVE (pick task)
    AG->>MCP: Parse PDF, extract images
    AG->>RD: PUBLISH status update
    RD->>BE: Status message
    BE->>FE: WS: "Analyzing schematic..."

    AG->>MCP: Search components on TME
    AG->>RD: PUBLISH status update
    RD->>BE: Status message
    BE->>FE: WS: "Searching TME for 10 components..."

    AG->>MCP: Generate CSV exports
    AG->>RD: PUBLISH result
    RD->>BE: Result message
    BE->>FE: WS: BOM data + exports
    FE->>U: Render BOM table
```

### Environment dependencies

```mermaid
graph LR
    subgraph Required API Keys
        A1[ANTHROPIC_API_KEY<br/>LLM inference]
        A2[TME_APP_TOKEN<br/>+ TME_APP_SECRET<br/>Component search]
        A3[SUPABASE_URL<br/>+ SUPABASE_KEY<br/>Database]
    end

    subgraph Internal - auto configured
        I1[REDIS_URL<br/>+ REDIS_PASSWORD]
        I2[MINIO_ENDPOINT<br/>+ credentials]
        I3[CORS_ORIGINS<br/>Allowed frontends]
    end

    subgraph Optional
        O1[ANTHROPIC_MODEL<br/>default: claude-sonnet-4-6]
        O2[TME_LANGUAGE / TME_COUNTRY<br/>default: PL]
        O3[AGENT_MAX_CONCURRENT_TASKS<br/>default: 50]
    end

    A1 --> BE[Backend + Agent]
    A2 --> MTME[mcp-tme]
    A3 --> BE
    I1 --> BE
    I1 --> AGT[Agent]
    I2 --> MDOC[mcp-documents]
    I2 --> MEXP[mcp-export]

    style A1 fill:#e74c3c,color:#fff
    style A2 fill:#e74c3c,color:#fff
    style A3 fill:#e74c3c,color:#fff
```

---

## Quick start (local development)

```bash
git clone https://github.com/MrFishPL/zane.git && cd zane
cp .env.example .env
```

Fill in your API keys in `.env`:

| Variable | Where to get it |
|----------|----------------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `TME_APP_TOKEN` + `TME_APP_SECRET` | [developers.tme.eu](https://developers.tme.eu) |
| `SUPABASE_URL` + `SUPABASE_KEY` | [supabase.com](https://supabase.com) |

Then start:

```bash
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000).

---

## Deploy to production

### Prerequisites

- A Linux server with SSH access
- Docker and Docker Compose installed (the deploy script auto-installs if missing)

### Option 1: GitHub Actions (automated)

Every push to `master` auto-deploys via GitHub Actions.

**Setup (one-time):**

```bash
# Set deployment secrets
gh secret set DEPLOY_HOST -b "your-server-ip"
gh secret set DEPLOY_USER -b "root"
gh secret set DEPLOY_PASSWORD -b "your-ssh-password"

# Set the .env file as a secret (contains all API keys)
gh secret set DOTENV < .env
```

Push to `master` and the workflow deploys automatically:
- Clones/pulls the repo to `/opt/zane` on the server
- Writes `.env` from the `DOTENV` secret
- Runs `docker compose up --build -d`

### Option 2: Manual deployment

```bash
# On your server
git clone https://github.com/MrFishPL/zane.git /opt/zane
cd /opt/zane

# Create .env with your API keys
cp .env.example .env
nano .env

# Add your server IP to CORS_ORIGINS
# CORS_ORIGINS=http://localhost:3000,http://YOUR_SERVER_IP:3000

# Build and start
docker compose up --build -d

# Check status
docker compose ps
```

### Post-deploy checklist

- [ ] Set `CORS_ORIGINS` in `.env` to include your server's public URL
- [ ] Change `REDIS_PASSWORD` from the default
- [ ] Change `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` from defaults
- [ ] Verify all containers are healthy: `docker compose ps`
- [ ] Test the frontend at `http://YOUR_SERVER_IP:3000`
- [ ] (Optional) Set up HTTPS with a reverse proxy (nginx/Caddy) in front of ports 3000 and 8000

### Updating

```bash
cd /opt/zane
git pull origin master
docker compose up --build -d
```

Or just push to `master` if GitHub Actions is configured.

---

## Development

```bash
# Run all services
docker compose up --build

# Run a single service
docker compose up --build backend

# Follow logs
docker compose logs -f agent

# Run Python tests
cd backend && pip install -r requirements.txt && pytest
cd agent && pip install -r requirements.txt && pytest

# Run frontend tests
cd frontend && npm install && npm test
```

### Observability

- **Grafana**: [http://localhost:3001](http://localhost:3001) (admin/admin)
- **Loki logs**: Accessible through Grafana dashboards
- **MinIO Console**: [http://localhost:9001](http://localhost:9001)

To enable Docker log shipping to Loki:

```bash
# Install the Loki Docker driver (one-time)
docker plugin install grafana/loki-docker-driver:latest --alias loki --grant-all-permissions

# Start with Loki logging overlay
docker compose -f docker-compose.yml -f docker-compose.override.loki.yml up -d
```

---

## External services

| Service | Purpose | Sign up |
|---------|---------|---------|
| **Anthropic API** | Claude LLM for vision + reasoning | [console.anthropic.com](https://console.anthropic.com) |
| **TME API** | Electronic component search, pricing, stock | [developers.tme.eu](https://developers.tme.eu) |
| **Supabase** | PostgreSQL database (conversations, messages) | [supabase.com](https://supabase.com) |

## License

Private repository.
