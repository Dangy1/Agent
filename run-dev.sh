#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/.dev-logs"
PID_FILE="$LOG_DIR/run-dev.pids"
ACTION="${1:-start}"

LANGGRAPH_PORT="${LANGGRAPH_PORT:-2024}"
ORAN_MCP_API_PORT="${ORAN_MCP_API_PORT:-8010}"
UAV_API_PORT="${UAV_API_PORT:-${UAV_UTM_API_PORT:-8020}}"
UTM_API_PORT="${UTM_API_PORT:-8021}"
NETWORK_API_PORT="${NETWORK_API_PORT:-8022}"
MISSION_SUPERVISOR_API_PORT="${MISSION_SUPERVISOR_API_PORT:-8023}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

START_FRONTEND="${START_FRONTEND:-1}"
START_MISSION_SUPERVISOR="${START_MISSION_SUPERVISOR:-1}"
USE_RELOAD="${USE_RELOAD:-1}"
LANGGRAPH_NO_RELOAD="${LANGGRAPH_NO_RELOAD:-1}"
ALLOW_EXISTING_PORTS="${ALLOW_EXISTING_PORTS:-0}"
FAA_POSTGIS_ENABLED="${FAA_POSTGIS_ENABLED:-1}"
FAA_POSTGIS_PROFILE="${FAA_POSTGIS_PROFILE:-runtime}"
FAA_POSTGIS_REQUIRED="${FAA_POSTGIS_REQUIRED:-0}"
FAA_POSTGIS_DIR="${FAA_POSTGIS_DIR:-$ROOT_DIR/backend/airspace_faa}"
FAA_APPLY_SCHEMA_SOURCE_ON_START="${FAA_APPLY_SCHEMA_SOURCE_ON_START:-0}"
FAA_RUN_SAMPLE_SOURCE_ON_START="${FAA_RUN_SAMPLE_SOURCE_ON_START:-0}"
FAA_VERIFY_SAMPLE_SOURCE_ON_START="${FAA_VERIFY_SAMPLE_SOURCE_ON_START:-0}"
FAA_SAMPLE_SOURCE_REQUIRED="${FAA_SAMPLE_SOURCE_REQUIRED:-0}"
AUTO_SEED_USS_LOCAL="${AUTO_SEED_USS_LOCAL:-1}"
AUTO_SEED_PARTICIPANT_ID="${AUTO_SEED_PARTICIPANT_ID:-uss-local-user-1}"
AUTO_SEED_USS_BASE_URL="${AUTO_SEED_USS_BASE_URL:-http://127.0.0.1:9000}"
AUTO_SEED_USS_ROLES="${AUTO_SEED_USS_ROLES:-uss}"
AUTO_SEED_USS_STATUS="${AUTO_SEED_USS_STATUS:-active}"
AUTO_SEED_TIMEOUT_S="${AUTO_SEED_TIMEOUT_S:-20}"
UTM_SERVICE_TOKEN_FOR_SEED="${UTM_SERVICE_TOKEN_FOR_SEED:-${UTM_SERVICE_TOKEN:-local-dev-token}}"
UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED="${UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED:-1}"
UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S="${UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S:-1.0}"
UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S="${UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S:-3.0}"
UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE="${UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE:-20}"
UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS="${UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS:-8}"

MANAGED_PORTS=(
  "$LANGGRAPH_PORT"
  "$ORAN_MCP_API_PORT"
  "$UAV_API_PORT"
  "$UTM_API_PORT"
  "$NETWORK_API_PORT"
  "$MISSION_SUPERVISOR_API_PORT"
  "$FRONTEND_PORT"
)

mkdir -p "$LOG_DIR"
: > "$PID_FILE"

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

pick_python() {
  if have_cmd python3; then
    echo "python3"
  elif have_cmd python; then
    echo "python"
  else
    echo ""
  fi
}

port_in_use() {
  local port="$1"
  if have_cmd lsof; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if have_cmd ss; then
    ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port "
    return $?
  fi
  return 1
}

print_ports() {
  echo "Managed service ports:"
  echo "  LANGGRAPH_PORT=$LANGGRAPH_PORT"
  echo "  ORAN_MCP_API_PORT=$ORAN_MCP_API_PORT"
  echo "  UAV_API_PORT=$UAV_API_PORT"
  echo "  UTM_API_PORT=$UTM_API_PORT"
  echo "  NETWORK_API_PORT=$NETWORK_API_PORT"
  echo "  MISSION_SUPERVISOR_API_PORT=$MISSION_SUPERVISOR_API_PORT"
  echo "  FRONTEND_PORT=$FRONTEND_PORT"
}

port_pids() {
  local port="$1"
  if have_cmd lsof; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NF'
    return 0
  fi
  if have_cmd ss; then
    ss -ltnp "( sport = :$port )" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' \
      | awk 'NF' \
      | sort -u
    return 0
  fi
  return 1
}

stop_managed_services() {
  local any=0
  local -a pids=()
  local port pid
  for port in "${MANAGED_PORTS[@]}"; do
    while read -r pid; do
      [[ -n "${pid:-}" ]] || continue
      pids+=("$pid")
    done < <(port_pids "$port" || true)
  done
  if [[ ${#pids[@]} -eq 0 ]]; then
    echo "No managed services are listening on configured ports."
    return 0
  fi
  mapfile -t pids < <(printf "%s\n" "${pids[@]}" | sort -u)
  echo "Stopping managed services on configured ports..."
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      any=1
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  sleep 1
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
  rm -f "$PID_FILE"
  if [[ "$any" == "1" ]]; then
    echo "Managed services stopped."
  else
    echo "No managed services needed stopping."
  fi
}

wait_for_http_ready() {
  local url="$1"
  local timeout_s="$2"
  local waited=0
  while (( waited < timeout_s )); do
    if curl -fsS --max-time 1 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  return 1
}

seed_local_uss_participant() {
  [[ "$AUTO_SEED_USS_LOCAL" == "1" ]] || return 0
  if ! have_cmd curl; then
    echo "Warning: curl not found; skipping AUTO_SEED_USS_LOCAL"
    return 0
  fi
  local utm_base="http://127.0.0.1:${UTM_API_PORT}"
  if ! wait_for_http_ready "$utm_base/api/utm/sync" "$AUTO_SEED_TIMEOUT_S"; then
    echo "Warning: UTM API not ready within ${AUTO_SEED_TIMEOUT_S}s; skipping AUTO_SEED_USS_LOCAL"
    return 0
  fi
  local payload
  payload="$("$PYTHON_BIN" - <<'PY'
import json, os

roles = [r.strip() for r in os.getenv("AUTO_SEED_USS_ROLES", "uss").split(",") if r.strip()]
if not roles:
    roles = ["uss"]
obj = {
    "participant_id": os.getenv("AUTO_SEED_PARTICIPANT_ID", "uss-local-user-1"),
    "uss_base_url": os.getenv("AUTO_SEED_USS_BASE_URL", "http://127.0.0.1:9000"),
    "roles": roles,
    "status": os.getenv("AUTO_SEED_USS_STATUS", "active"),
}
print(json.dumps(obj))
PY
)"
  if curl -fsS -X POST "$utm_base/api/utm/dss/participants" \
    -H "Authorization: Bearer ${UTM_SERVICE_TOKEN_FOR_SEED}" \
    -H "Content-Type: application/json" \
    -d "$payload" >/dev/null 2>&1; then
    echo "Auto-seeded DSS participant: $AUTO_SEED_PARTICIPANT_ID"
  else
    echo "Warning: failed to auto-seed DSS participant ($AUTO_SEED_PARTICIPANT_ID)"
  fi
}

_faa_postgis_error_or_warn() {
  local message="$1"
  if [[ "$FAA_POSTGIS_REQUIRED" == "1" ]]; then
    echo "Error: $message"
    exit 1
  fi
  echo "Warning: $message"
}

_faa_sample_error_or_warn() {
  local message="$1"
  if [[ "$FAA_SAMPLE_SOURCE_REQUIRED" == "1" ]]; then
    echo "Error: $message"
    exit 1
  fi
  echo "Warning: $message"
}

ensure_faa_postgis() {
  [[ "$FAA_POSTGIS_ENABLED" == "1" ]] || return 0

  local env_example="$FAA_POSTGIS_DIR/.env.example"
  local env_file="$FAA_POSTGIS_DIR/.env"
  local bootstrap_script="$FAA_POSTGIS_DIR/scripts/bootstrap_postgis.sh"

  if [[ "$FAA_POSTGIS_PROFILE" == "source" ]]; then
    bootstrap_script="$FAA_POSTGIS_DIR/scripts/bootstrap_postgis_source.sh"
  fi

  if [[ ! -d "$FAA_POSTGIS_DIR" ]]; then
    _faa_postgis_error_or_warn "FAA PostGIS directory not found: $FAA_POSTGIS_DIR"
    return 0
  fi
  if [[ ! -f "$env_file" && -f "$env_example" ]]; then
    cp "$env_example" "$env_file"
    echo "Created $env_file from .env.example"
  fi
  if [[ ! -f "$env_file" ]]; then
    _faa_postgis_error_or_warn "FAA PostGIS .env missing at $env_file"
    return 0
  fi
  if [[ ! -f "$bootstrap_script" ]]; then
    _faa_postgis_error_or_warn "FAA PostGIS bootstrap script missing: $bootstrap_script"
    return 0
  fi
  if ! have_cmd docker; then
    _faa_postgis_error_or_warn "docker is not available; cannot start FAA PostGIS"
    return 0
  fi

  echo "Ensuring FAA PostGIS is running (profile=$FAA_POSTGIS_PROFILE)..."
  if ! bash "$bootstrap_script"; then
    _faa_postgis_error_or_warn "FAA PostGIS bootstrap failed (profile=$FAA_POSTGIS_PROFILE)"
    return 0
  fi
}

maybe_run_faa_sample_source() {
  if [[ "$FAA_APPLY_SCHEMA_SOURCE_ON_START" != "1" && "$FAA_RUN_SAMPLE_SOURCE_ON_START" != "1" && "$FAA_VERIFY_SAMPLE_SOURCE_ON_START" != "1" ]]; then
    return 0
  fi
  if [[ "$FAA_POSTGIS_PROFILE" != "source" ]]; then
    _faa_sample_error_or_warn "FAA sample source test requested but FAA_POSTGIS_PROFILE=$FAA_POSTGIS_PROFILE (set FAA_POSTGIS_PROFILE=source)."
    return 0
  fi
  if [[ ! -d "$FAA_POSTGIS_DIR" ]]; then
    _faa_sample_error_or_warn "FAA directory not found: $FAA_POSTGIS_DIR"
    return 0
  fi
  if [[ ! -f "$FAA_POSTGIS_DIR/.env" ]]; then
    _faa_sample_error_or_warn "FAA .env not found: $FAA_POSTGIS_DIR/.env"
    return 0
  fi
  if ! have_cmd make; then
    _faa_sample_error_or_warn "make is required for FAA source sample test."
    return 0
  fi
  if ! have_cmd docker; then
    _faa_sample_error_or_warn "docker is required for FAA source sample test."
    return 0
  fi

  local log_file="$LOG_DIR/faa-sample-ingest.log"
  : > "$log_file"

  if [[ "$FAA_APPLY_SCHEMA_SOURCE_ON_START" == "1" ]]; then
    echo "Running FAA source schema apply (make apply-schema-source)..."
    if ! (cd "$FAA_POSTGIS_DIR" && make apply-schema-source) >>"$log_file" 2>&1; then
      _faa_sample_error_or_warn "FAA source schema apply failed (see $log_file)."
      return 0
    fi
  fi

  if [[ "$FAA_RUN_SAMPLE_SOURCE_ON_START" == "1" ]]; then
    echo "Running FAA source sample ingest (make ingest-sample-source)..."
    if ! (cd "$FAA_POSTGIS_DIR" && make ingest-sample-source) >>"$log_file" 2>&1; then
      _faa_sample_error_or_warn "FAA source sample ingest failed (see $log_file)."
      return 0
    fi
  fi

  if [[ "$FAA_VERIFY_SAMPLE_SOURCE_ON_START" == "1" ]]; then
    echo "Verifying FAA source ingestion_run status..."
    (
      cd "$FAA_POSTGIS_DIR"
      # shellcheck disable=SC1091
      set -a && source .env && set +a
      docker compose --env-file .env -f docker-compose.postgis-source.yml exec -T postgis-source \
        psql -U "${POSTGRES_USER:-faa}" -d "${POSTGRES_DB:-faa_airspace}" -c \
        "SELECT run_id, status, started_at, finished_at FROM faa_airspace.ingestion_run ORDER BY started_at DESC LIMIT 3;"
    ) >>"$log_file" 2>&1 || _faa_sample_error_or_warn "FAA source ingestion verification failed (see $log_file)."
  fi

  echo "FAA source sample tasks finished. Log: $log_file"
}

if [[ "$ACTION" == "ports" ]]; then
  print_ports
  exit 0
fi

if [[ "$ACTION" == "stop" ]]; then
  print_ports
  stop_managed_services
  exit 0
fi

if [[ "$ACTION" == "restart" ]]; then
  print_ports
  stop_managed_services
  echo
  echo "Restarting managed services..."
elif [[ "$ACTION" != "start" ]]; then
  echo "Usage: $0 [start|stop|restart|ports]"
  exit 1
fi

PYTHON_BIN="$(pick_python)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Error: python3/python not found"
  exit 1
fi

if ! have_cmd uvicorn; then
  echo "Error: uvicorn not found in PATH"
  exit 1
fi

if ! have_cmd langgraph; then
  echo "Error: langgraph CLI not found in PATH"
  echo "Install/activate the environment that has langgraph CLI, then rerun."
  exit 1
fi

if [[ "$START_FRONTEND" == "1" ]] && ! have_cmd npm; then
  echo "Error: npm not found in PATH (required when START_FRONTEND=1)"
  exit 1
fi

ensure_faa_postgis
maybe_run_faa_sample_source

check_port_free() {
  local port="$1"
  local name="$2"
  if port_in_use "$port"; then
    echo "Error: port $port is already in use ($name)"
    exit 1
  fi
}

SKIP_LANGGRAPH=0
SKIP_ORAN_MCP_API=0
SKIP_UAV_API=0
SKIP_UTM_API=0
SKIP_NETWORK_API=0
SKIP_MISSION_SUPERVISOR=0
SKIP_FRONTEND=0

check_port_or_reuse() {
  local port="$1"
  local name="$2"
  local flag_var="$3"
  if port_in_use "$port"; then
    if [[ "$ALLOW_EXISTING_PORTS" == "1" ]]; then
      printf -v "$flag_var" "1"
      echo "Warning: port $port already in use ($name); reusing existing service"
      return 0
    fi
    echo "Error: port $port is already in use ($name)"
    echo "Tip: stop the existing process, or run with ALLOW_EXISTING_PORTS=1 to reuse it."
    exit 1
  fi
}

check_port_or_reuse "$LANGGRAPH_PORT" "LangGraph backend" SKIP_LANGGRAPH
check_port_or_reuse "$ORAN_MCP_API_PORT" "O-RAN MCP config API" SKIP_ORAN_MCP_API
check_port_or_reuse "$UAV_API_PORT" "UAV simulator API" SKIP_UAV_API
check_port_or_reuse "$UTM_API_PORT" "UTM simulator API" SKIP_UTM_API
check_port_or_reuse "$NETWORK_API_PORT" "Network mission API" SKIP_NETWORK_API
if [[ "$START_MISSION_SUPERVISOR" == "1" ]]; then
  check_port_or_reuse "$MISSION_SUPERVISOR_API_PORT" "Mission supervisor API" SKIP_MISSION_SUPERVISOR
fi
if [[ "$START_FRONTEND" == "1" ]]; then
  check_port_or_reuse "$FRONTEND_PORT" "Frontend" SKIP_FRONTEND
fi

append_pid() {
  local pid="$1"
  local name="$2"
  echo "$pid $name" >> "$PID_FILE"
}

start_bg() {
  local name="$1"
  local workdir="$2"
  local logfile="$3"
  shift 3

  echo "Starting $name..."
  (
    cd "$workdir"
    exec "$@" >>"$logfile" 2>&1
  ) &
  local pid=$!
  append_pid "$pid" "$name"
  echo "  pid=$pid log=$logfile"
}

cleanup() {
  if [[ ! -f "$PID_FILE" ]]; then
    exit 0
  fi
  echo
  echo "Stopping services..."
  tac "$PID_FILE" 2>/dev/null | while read -r pid _name; do
    [[ -n "${pid:-}" ]] || continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  sleep 1
  tac "$PID_FILE" 2>/dev/null | while read -r pid _name; do
    [[ -n "${pid:-}" ]] || continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
  rm -f "$PID_FILE"
}

trap cleanup INT TERM EXIT

reuse_note() {
  if [[ "${1:-0}" == "1" ]]; then
    printf " (reused)"
  fi
}

UVICORN_RELOAD_FLAG=()
if [[ "$USE_RELOAD" == "1" ]]; then
  UVICORN_RELOAD_FLAG=(--reload)
fi

if [[ "$SKIP_LANGGRAPH" == "0" ]]; then
  LANGGRAPH_FLAGS=(--port "$LANGGRAPH_PORT")
  if [[ "$LANGGRAPH_NO_RELOAD" == "1" ]]; then
    # langgraph dev writes runtime state under backend/.langgraph_api, which can
    # trigger a self-reload loop when backend/ is the watched directory.
    LANGGRAPH_FLAGS+=(--no-reload)
  fi
  start_bg \
    "langgraph-dev" \
    "$BACKEND_DIR" \
    "$LOG_DIR/langgraph-dev.log" \
    langgraph dev "${LANGGRAPH_FLAGS[@]}"
fi

if [[ "$SKIP_ORAN_MCP_API" == "0" ]]; then
  start_bg \
    "oran-mcp-api" \
    "$BACKEND_DIR" \
    "$LOG_DIR/oran-mcp-api.log" \
    uvicorn oran_agent.api:app --host 0.0.0.0 --port "$ORAN_MCP_API_PORT" "${UVICORN_RELOAD_FLAG[@]}"
fi

if [[ "$SKIP_UAV_API" == "0" ]]; then
  start_bg \
    "uav-sim-api" \
    "$BACKEND_DIR" \
    "$LOG_DIR/uav-sim-api.log" \
    uvicorn uav_agent.api:app --host 0.0.0.0 --port "$UAV_API_PORT" "${UVICORN_RELOAD_FLAG[@]}"
fi

if [[ "$SKIP_UTM_API" == "0" ]]; then
  start_bg \
    "utm-sim-api" \
    "$BACKEND_DIR" \
    "$LOG_DIR/utm-sim-api.log" \
    env \
    "UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED=${UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED}" \
    "UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S=${UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S}" \
    "UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S=${UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S}" \
    "UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE=${UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE}" \
    "UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS=${UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS}" \
    uvicorn utm_agent.api:app --host 0.0.0.0 --port "$UTM_API_PORT" "${UVICORN_RELOAD_FLAG[@]}"
fi

if [[ "$SKIP_NETWORK_API" == "0" ]]; then
  start_bg \
    "network-mission-api" \
    "$BACKEND_DIR" \
    "$LOG_DIR/network-mission-api.log" \
    env "UAV_AGENT_BASE_URL=http://127.0.0.1:${UAV_API_PORT}" "UTM_AGENT_BASE_URL=http://127.0.0.1:${UTM_API_PORT}" \
    uvicorn network_agent.api:app --host 0.0.0.0 --port "$NETWORK_API_PORT" "${UVICORN_RELOAD_FLAG[@]}"
fi

if [[ "$START_MISSION_SUPERVISOR" == "1" && "$SKIP_MISSION_SUPERVISOR" == "0" ]]; then
  start_bg \
    "mission-supervisor-api" \
    "$BACKEND_DIR" \
    "$LOG_DIR/mission-supervisor-api.log" \
    uvicorn mission_supervisor_agent.api:app --host 0.0.0.0 --port "$MISSION_SUPERVISOR_API_PORT" "${UVICORN_RELOAD_FLAG[@]}"
fi

if [[ "$START_FRONTEND" == "1" && "$SKIP_FRONTEND" == "0" ]]; then
  start_bg \
    "frontend-vite" \
    "$FRONTEND_DIR" \
    "$LOG_DIR/frontend-vite.log" \
    npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"
fi

seed_local_uss_participant

echo
echo "Services started"
echo "  LangGraph backend (O-RAN/UAV/UTM/mission supervisor graphs): http://127.0.0.1:${LANGGRAPH_PORT}$(reuse_note "$SKIP_LANGGRAPH")"
echo "  O-RAN MCP config API: http://127.0.0.1:${ORAN_MCP_API_PORT}$(reuse_note "$SKIP_ORAN_MCP_API")"
echo "  UAV simulator API: http://127.0.0.1:${UAV_API_PORT}$(reuse_note "$SKIP_UAV_API")"
echo "  UTM simulator API: http://127.0.0.1:${UTM_API_PORT}$(reuse_note "$SKIP_UTM_API")"
echo "  Network mission API: http://127.0.0.1:${NETWORK_API_PORT}$(reuse_note "$SKIP_NETWORK_API")"
if [[ "$START_MISSION_SUPERVISOR" == "1" ]]; then
  echo "  Mission supervisor API: http://127.0.0.1:${MISSION_SUPERVISOR_API_PORT}$(reuse_note "$SKIP_MISSION_SUPERVISOR")"
fi
if [[ "$START_FRONTEND" == "1" ]]; then
  echo "  Frontend: http://127.0.0.1:${FRONTEND_PORT}$(reuse_note "$SKIP_FRONTEND")"
  echo "    O-RAN page:   http://127.0.0.1:${FRONTEND_PORT}/#/oran"
  echo "    UAV page:     http://127.0.0.1:${FRONTEND_PORT}/#/uav"
  echo "    UTM page:     http://127.0.0.1:${FRONTEND_PORT}/#/utm"
  echo "    Network page: http://127.0.0.1:${FRONTEND_PORT}/#/network"
fi
if [[ "$AUTO_SEED_USS_LOCAL" == "1" ]]; then
  echo "  DSS participant auto-seed: ${AUTO_SEED_PARTICIPANT_ID} (${AUTO_SEED_USS_BASE_URL})"
fi
echo "  DSS callback dispatcher: enabled=${UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED} interval_s=${UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S} timeout_s=${UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S} batch=${UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE} max_attempts=${UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS}"
echo
echo "Logs:"
echo "  $LOG_DIR/langgraph-dev.log"
echo "  $LOG_DIR/oran-mcp-api.log"
echo "  $LOG_DIR/uav-sim-api.log"
echo "  $LOG_DIR/utm-sim-api.log"
echo "  $LOG_DIR/network-mission-api.log"
if [[ "$START_MISSION_SUPERVISOR" == "1" ]]; then
  echo "  $LOG_DIR/mission-supervisor-api.log"
fi
if [[ "$START_FRONTEND" == "1" ]]; then
  echo "  $LOG_DIR/frontend-vite.log"
fi
echo
echo "Press Ctrl+C to stop all services."
if [[ "$ALLOW_EXISTING_PORTS" == "1" ]]; then
  echo "Note: reused services were not started by this script and will not be stopped by it."
fi

# Keep script attached and surface early exits.
while true; do
  if [[ -f "$PID_FILE" ]]; then
    while read -r pid name; do
      [[ -n "${pid:-}" ]] || continue
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        echo "Service exited unexpectedly: $name (pid=$pid)"
        echo "Check logs in $LOG_DIR"
        exit 1
      fi
    done < "$PID_FILE"
  fi
  sleep 2
done
