# UTM/UAV/FAA Multi-Service Stack

This repo contains one integrated local stack:

- LangGraph runtime for multi-agent graphs
- O-RAN MCP config API
- UAV simulator API
- UTM API
- Network mission API
- Mission supervisor API
- FAA airspace backend (PostGIS + ingestion scripts)
- React/Vite frontend console

For a dedicated clean-machine runbook, see:

- [README_ZERO_TO_RUN.md](/home/dang/agent_test/README_ZERO_TO_RUN.md)
- [Multi-Agent A2A/MCP/LangGraph Architecture](/home/dang/agent_test/docs/architecture/MULTI_AGENT_A2A_MCP_LANGGRAPH.md)
- [Agent-System Skills (LangGraph/LangChain)](/home/dang/agent_test/docs/architecture/AGENT_SYSTEM_SKILLS.md)

## Start From Zero

### 1) Prerequisites

- Conda (Miniconda/Anaconda)
- Docker + Docker Compose plugin (for FAA PostGIS)
- Bash shell

### 2) Clone and enter repo

```bash
git clone <your-repo-url> agent_test
cd agent_test
```

### 3) Create the unified conda env

```bash
conda env create -f environment.langchain.yml
conda activate langchain
```

If the env already exists:

```bash
conda env update -n langchain -f environment.langchain.yml --prune
conda activate langchain
```

### 4) One-time bootstrap + start

```bash
./bash.sh once
```

What this does:

- syncs `langchain` env
- installs frontend dependencies
- prepares `backend/airspace_faa/.env`
- attempts FAA PostGIS bootstrap
- installs conda activate/deactivate hooks
- starts the stack detached

## Daily Usage

### Start/stop/status

```bash
./bash.sh start
./bash.sh stop
./bash.sh restart
./bash.sh status
```

### Setup only (no start)

```bash
./bash.sh setup
```

## LLM Provider Selection (Ollama Default)

By default, agents use Ollama via `OLLAMA_URL`/`OLLAMA_MODEL`.

If Ollama is unavailable, switch to OpenAI with environment variables:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=<your_key>
export OPENAI_MODEL=gpt-4o-mini
```

Optional fallback behavior when configured provider is Ollama:

```bash
export LLM_FALLBACK_TO_OPENAI=1
```

Runtime API endpoints (port `8010`):

- `GET /api/llm/config`
- `PATCH /api/llm/config`
- `DELETE /api/llm/config`

## Conda Auto-Start

After hook install, this auto-starts the stack:

```bash
conda activate langchain
```

Controls:

- `LANGCHAIN_DISABLE_AUTOSTART=1` disables start-on-activate
- `LANGCHAIN_AUTOSTOP_ON_DEACTIVATE=1` enables stop-on-deactivate

## Ports

- LangGraph: `2024`
- O-RAN MCP API: `8010`
- UAV API: `8020`
- UTM API: `8021`
- Network API: `8022`
- Mission Supervisor API: `8023`
- DSS API: `8024`
- USS API: `8025`
- Frontend: `5173`

## Smoke Check Commands

Run after stack start:

```bash
curl -sS http://127.0.0.1:2024/openapi.json | jq -r .openapi
curl -sS http://127.0.0.1:8010/api/mcp/config | jq -r .status
curl -sS http://127.0.0.1:8020/api/uav/sim/fleet | jq -r .status
curl -sS -H "Authorization: Bearer local-dev-token" http://127.0.0.1:8021/api/utm/sync | jq -r .status
curl -sS "http://127.0.0.1:8022/api/network/mission/state?airspace_segment=sector-A3&selected_uav_id=uav-1" | jq -r '.status // "success"'
curl -sS http://127.0.0.1:8023/api/mission | jq -r .status
curl -sS http://127.0.0.1:8024/api/dss/state | jq -r .status
curl -sS http://127.0.0.1:8025/api/uss/state | jq -r .status
curl -I http://127.0.0.1:5173
```

Note: `/api/utm/sync` requires the bearer token above in local dev.

Mission protocol trace (A2A + MCP per mission):

```bash
curl -sS "http://127.0.0.1:8023/api/mission/<mission_id>/protocol-trace?limit=200&include_replayed=true" | jq
```

Agent-system skills (project-local, not Codex skills):

```bash
curl -sS http://127.0.0.1:8023/api/mission/skills | jq
curl -sS -X POST http://127.0.0.1:8023/api/mission/skills/match \
  -H "Content-Type: application/json" \
  -d '{"request_text":"run dss conflict and subscription checks"}' | jq
```

## UAV/UTM Procedure MCP Server

For high-level UAV/UTM MCP tools (prepare, plan, submit, launch/step, replan), use:

- [backend/others/MCP_UAV_UTM_PROCEDURES.md](/home/dang/agent_test/backend/others/MCP_UAV_UTM_PROCEDURES.md)
- [backend/others/MCP_UAV_UTM_STRICT_OPS.md](/home/dang/agent_test/backend/others/MCP_UAV_UTM_STRICT_OPS.md)

Runtime MCP profiles available:

- `uav-utm-procedures-stdio`
- `uav-utm-strict-ops-stdio`

Quick preset switch (no manual curl):

```bash
./scripts/mcp_profile_preset.sh show
./scripts/mcp_profile_preset.sh procedures
./scripts/mcp_profile_preset.sh strict-ops
```

## FAA Backend Notes

FAA assets live in `backend/airspace_faa`.

- Runtime compose file: `backend/airspace_faa/docker-compose.postgis.yml`
- Source-build compose file: `backend/airspace_faa/docker-compose.postgis-source.yml`

Check FAA DB containers:

```bash
docker compose --env-file backend/airspace_faa/.env -f backend/airspace_faa/docker-compose.postgis.yml ps
docker compose --env-file backend/airspace_faa/.env -f backend/airspace_faa/docker-compose.postgis-source.yml ps
```

If Docker is unavailable or blocked, start stack without FAA bootstrap:

```bash
FAA_POSTGIS_ENABLED=0 ./bash.sh restart
```

## Troubleshooting

- If ports are already used: `ALLOW_EXISTING_PORTS=1 ./bash.sh start`
- If frontend is not needed: `START_FRONTEND=0 ./bash.sh restart`
- If mission supervisor is not needed: `START_MISSION_SUPERVISOR=0 ./bash.sh restart`
- If DSS/USS APIs are not needed: `START_DSS_AGENT=0 START_USS_AGENT=0 ./bash.sh restart`
- If FAA bootstrap is slow/blocked: `FAA_POSTGIS_ENABLED=0 ./bash.sh start`
