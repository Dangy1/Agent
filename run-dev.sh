#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/.dev-logs"
PID_FILE="$LOG_DIR/run-dev.pids"

LANGGRAPH_PORT="${LANGGRAPH_PORT:-2024}"
ORAN_MCP_API_PORT="${ORAN_MCP_API_PORT:-8010}"
UAV_API_PORT="${UAV_API_PORT:-${UAV_UTM_API_PORT:-8020}}"
UTM_API_PORT="${UTM_API_PORT:-8021}"
NETWORK_API_PORT="${NETWORK_API_PORT:-8022}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

START_FRONTEND="${START_FRONTEND:-1}"
USE_RELOAD="${USE_RELOAD:-1}"
ALLOW_EXISTING_PORTS="${ALLOW_EXISTING_PORTS:-0}"

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
  start_bg \
    "langgraph-dev" \
    "$BACKEND_DIR" \
    "$LOG_DIR/langgraph-dev.log" \
    langgraph dev --port "$LANGGRAPH_PORT"
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

if [[ "$START_FRONTEND" == "1" && "$SKIP_FRONTEND" == "0" ]]; then
  start_bg \
    "frontend-vite" \
    "$FRONTEND_DIR" \
    "$LOG_DIR/frontend-vite.log" \
    npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT"
fi

echo
echo "Services started"
echo "  LangGraph backend (O-RAN/UAV/UTM/mission supervisor graphs): http://127.0.0.1:${LANGGRAPH_PORT}$(reuse_note "$SKIP_LANGGRAPH")"
echo "  O-RAN MCP config API: http://127.0.0.1:${ORAN_MCP_API_PORT}$(reuse_note "$SKIP_ORAN_MCP_API")"
echo "  UAV simulator API: http://127.0.0.1:${UAV_API_PORT}$(reuse_note "$SKIP_UAV_API")"
echo "  UTM simulator API: http://127.0.0.1:${UTM_API_PORT}$(reuse_note "$SKIP_UTM_API")"
echo "  Network mission API: http://127.0.0.1:${NETWORK_API_PORT}$(reuse_note "$SKIP_NETWORK_API")"
if [[ "$START_FRONTEND" == "1" ]]; then
  echo "  Frontend: http://127.0.0.1:${FRONTEND_PORT}$(reuse_note "$SKIP_FRONTEND")"
  echo "    O-RAN page:   http://127.0.0.1:${FRONTEND_PORT}/#/oran"
  echo "    UAV page:     http://127.0.0.1:${FRONTEND_PORT}/#/uav"
  echo "    UTM page:     http://127.0.0.1:${FRONTEND_PORT}/#/utm"
  echo "    Network page: http://127.0.0.1:${FRONTEND_PORT}/#/network"
fi
echo
echo "Logs:"
echo "  $LOG_DIR/langgraph-dev.log"
echo "  $LOG_DIR/oran-mcp-api.log"
echo "  $LOG_DIR/uav-sim-api.log"
echo "  $LOG_DIR/utm-sim-api.log"
echo "  $LOG_DIR/network-mission-api.log"
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
