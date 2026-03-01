# Zero-to-Run Guide

This guide starts the full project from a clean machine state.

## What You Get

- LangGraph runtime
- O-RAN MCP API
- UAV API
- UTM API
- Network API
- Mission Supervisor API
- Frontend (Vite)
- FAA backend (PostGIS + ingestion pipeline support)

## 1) Prerequisites

- Linux/macOS shell with `bash`
- Conda (Miniconda or Anaconda)
- Docker + Docker Compose plugin (for FAA PostGIS)
- Git

## 2) Clone

```bash
git clone <your-repo-url> agent_test
cd agent_test
```

## 3) Create the Unified Conda Env

```bash
conda env create -f environment.langchain.yml
conda activate langchain
```

If already created:

```bash
conda env update -n langchain -f environment.langchain.yml --prune
conda activate langchain
```

## 4) One-Time Setup + Start

```bash
./bash.sh once
```

This performs:

- env sync
- frontend dependency install
- FAA `.env` preparation
- FAA PostGIS bootstrap attempt
- conda hook install (auto-start on `conda activate langchain`)
- detached stack start

## 5) Daily Commands

```bash
./bash.sh start
./bash.sh stop
./bash.sh restart
./bash.sh status
```

Setup only:

```bash
./bash.sh setup
```

## 6) Verify Services

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

### LLM Provider (default Ollama, optional OpenAI)

Default behavior uses Ollama (`LLM_PROVIDER=ollama`).

To run with OpenAI instead:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=<your_key>
export OPENAI_MODEL=gpt-4o-mini
```

Runtime config API:

```bash
curl -sS http://127.0.0.1:8010/api/llm/config | jq
```

Expected status values are `success` for the APIs above.

Quick MCP preset switching:

```bash
./scripts/mcp_profile_preset.sh show
./scripts/mcp_profile_preset.sh procedures
./scripts/mcp_profile_preset.sh strict-ops
```

## 7) FAA Backend

FAA files are in `backend/airspace_faa`.

Compose (runtime image):

```bash
docker compose --env-file backend/airspace_faa/.env -f backend/airspace_faa/docker-compose.postgis.yml up -d
docker compose --env-file backend/airspace_faa/.env -f backend/airspace_faa/docker-compose.postgis.yml ps
```

Compose (source-built image):

```bash
docker compose --env-file backend/airspace_faa/.env -f backend/airspace_faa/docker-compose.postgis-source.yml up -d postgis-source
docker compose --env-file backend/airspace_faa/.env -f backend/airspace_faa/docker-compose.postgis-source.yml ps
```

### Run FAA sample test via `run-dev.sh`

`run-dev.sh` can now run FAA source schema + sample ingest automatically.

```bash
FAA_POSTGIS_PROFILE=source \
FAA_APPLY_SCHEMA_SOURCE_ON_START=1 \
FAA_RUN_SAMPLE_SOURCE_ON_START=1 \
FAA_VERIFY_SAMPLE_SOURCE_ON_START=1 \
./run-dev.sh restart
```

Optional strict mode (fail startup if FAA sample step fails):

```bash
FAA_SAMPLE_SOURCE_REQUIRED=1
```

FAA sample task log:

```bash
.dev-logs/faa-sample-ingest.log
```

## 8) Common Fallbacks

If Docker/FAA startup is blocked:

```bash
FAA_POSTGIS_ENABLED=0 ./bash.sh restart
```

If frontend is not needed:

```bash
START_FRONTEND=0 ./bash.sh restart
```

If mission supervisor API is not needed:

```bash
START_MISSION_SUPERVISOR=0 ./bash.sh restart
```

If DSS/USS APIs are not needed:

```bash
START_DSS_AGENT=0 START_USS_AGENT=0 ./bash.sh restart
```

If ports are already occupied and should be reused:

```bash
ALLOW_EXISTING_PORTS=1 ./bash.sh start
```

## 9) Conda Auto-Start Controls

Disable auto-start on activation:

```bash
export LANGCHAIN_DISABLE_AUTOSTART=1
```

Enable auto-stop on `conda deactivate`:

```bash
export LANGCHAIN_AUTOSTOP_ON_DEACTIVATE=1
```
