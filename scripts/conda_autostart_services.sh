#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/.dev-logs"
CONTROLLER_PID_FILE="$LOG_DIR/run-dev.autostart.pid"
AUTOSTART_LOG="$LOG_DIR/conda-autostart.log"
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

mkdir -p "$LOG_DIR"

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

pid_is_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" >/dev/null 2>&1
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

run_dev_already_running() {
  if ! have_cmd pgrep; then
    return 1
  fi
  pgrep -f "$ROOT_DIR/run-dev.sh" >/dev/null 2>&1
}

all_primary_ports_in_use() {
  local -a ports=("$LANGGRAPH_PORT" "$ORAN_MCP_API_PORT" "$UAV_API_PORT" "$UTM_API_PORT" "$NETWORK_API_PORT")
  if [[ "$START_MISSION_SUPERVISOR" == "1" ]]; then
    ports+=("$MISSION_SUPERVISOR_API_PORT")
  fi
  if [[ "$START_FRONTEND" == "1" ]]; then
    ports+=("$FRONTEND_PORT")
  fi
  local port
  for port in "${ports[@]}"; do
    if ! port_in_use "$port"; then
      return 1
    fi
  done
  return 0
}

start_stack() {
  local existing_pid=""
  if [[ -f "$CONTROLLER_PID_FILE" ]]; then
    existing_pid="$(cat "$CONTROLLER_PID_FILE" 2>/dev/null || true)"
  fi
  if pid_is_alive "$existing_pid"; then
    echo "Autostart controller is already running (pid=$existing_pid)."
    return 0
  fi
  rm -f "$CONTROLLER_PID_FILE"

  if run_dev_already_running; then
    echo "run-dev controller is already running; skipping autostart."
    return 0
  fi

  if all_primary_ports_in_use; then
    echo "All expected service ports are already in use; skipping autostart."
    return 0
  fi

  (
    cd "$ROOT_DIR"
    nohup env \
      "ALLOW_EXISTING_PORTS=1" \
      "FAA_POSTGIS_ENABLED=${FAA_POSTGIS_ENABLED:-1}" \
      "START_MISSION_SUPERVISOR=${START_MISSION_SUPERVISOR}" \
      "START_FRONTEND=${START_FRONTEND}" \
      "$ROOT_DIR/run-dev.sh" start >>"$AUTOSTART_LOG" 2>&1 &
    echo "$!" > "$CONTROLLER_PID_FILE"
  )

  sleep 1
  local new_pid
  new_pid="$(cat "$CONTROLLER_PID_FILE" 2>/dev/null || true)"
  if pid_is_alive "$new_pid"; then
    echo "Autostarted stack controller (pid=$new_pid)."
    return 0
  fi

  echo "Failed to autostart stack. Check $AUTOSTART_LOG"
  rm -f "$CONTROLLER_PID_FILE"
  return 1
}

stop_stack() {
  if [[ ! -f "$CONTROLLER_PID_FILE" ]]; then
    echo "No autostart controller PID file found."
    return 0
  fi

  local pid
  pid="$(cat "$CONTROLLER_PID_FILE" 2>/dev/null || true)"
  if ! pid_is_alive "$pid"; then
    echo "Autostart controller is not running."
    rm -f "$CONTROLLER_PID_FILE"
    return 0
  fi

  kill "$pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do
    if ! pid_is_alive "$pid"; then
      break
    fi
    sleep 0.2
  done
  if pid_is_alive "$pid"; then
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$CONTROLLER_PID_FILE"
  echo "Stopped autostart controller (pid=$pid)."
}

status_stack() {
  local pid=""
  if [[ -f "$CONTROLLER_PID_FILE" ]]; then
    pid="$(cat "$CONTROLLER_PID_FILE" 2>/dev/null || true)"
  fi
  if pid_is_alive "$pid"; then
    echo "Autostart controller: running (pid=$pid)"
  else
    echo "Autostart controller: not running"
  fi
  echo "Autostart log: $AUTOSTART_LOG"
}

case "$ACTION" in
  start)
    start_stack
    ;;
  stop)
    stop_stack
    ;;
  restart)
    stop_stack
    start_stack
    ;;
  status)
    status_stack
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status]"
    exit 1
    ;;
esac
