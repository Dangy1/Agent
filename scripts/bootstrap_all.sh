#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
FAA_DIR="$ROOT_DIR/backend/airspace_faa"
CONDA_FILE="$ROOT_DIR/environment.langchain.yml"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-langchain}"
ACTION="${1:-once}"

CONDA_SYNC_ENV="${CONDA_SYNC_ENV:-1}"
INSTALL_CONDA_HOOKS="${INSTALL_CONDA_HOOKS:-1}"
INSTALL_FRONTEND_DEPS="${INSTALL_FRONTEND_DEPS:-1}"
FAA_POSTGIS_ENABLED="${FAA_POSTGIS_ENABLED:-1}"
FAA_POSTGIS_PROFILE="${FAA_POSTGIS_PROFILE:-runtime}"
AUTO_START="${AUTO_START:-1}"
START_DETACHED="${START_DETACHED:-1}"

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

conda_env_exists() {
  have_cmd conda || return 1
  conda env list | awk '{print $1}' | grep -Fxq "$CONDA_ENV_NAME"
}

sync_conda_env() {
  [[ "$CONDA_SYNC_ENV" == "1" ]] || return 0
  if ! have_cmd conda; then
    echo "Warning: conda not found; skipping conda env sync."
    return 0
  fi
  if [[ ! -f "$CONDA_FILE" ]]; then
    echo "Error: missing conda env file: $CONDA_FILE"
    exit 1
  fi

  cd "$ROOT_DIR"
  if conda_env_exists; then
    echo "Updating conda env '$CONDA_ENV_NAME' from $CONDA_FILE ..."
    conda env update -n "$CONDA_ENV_NAME" -f "$CONDA_FILE" --prune
  else
    echo "Creating conda env '$CONDA_ENV_NAME' from $CONDA_FILE ..."
    conda env create -f "$CONDA_FILE"
  fi
}

ensure_frontend_deps() {
  [[ "$INSTALL_FRONTEND_DEPS" == "1" ]] || return 0
  local npm_mode="ci"
  if [[ ! -f "$FRONTEND_DIR/package-lock.json" ]]; then
    npm_mode="install"
  fi

  if conda_env_exists; then
    echo "Installing frontend dependencies via conda env '$CONDA_ENV_NAME' (npm $npm_mode) ..."
    conda run -n "$CONDA_ENV_NAME" npm --prefix "$FRONTEND_DIR" "$npm_mode"
    return 0
  fi

  if have_cmd npm; then
    echo "Installing frontend dependencies (npm $npm_mode) ..."
    npm --prefix "$FRONTEND_DIR" "$npm_mode"
    return 0
  fi

  echo "Warning: npm not available; skipping frontend dependency install."
}

prepare_faa_env() {
  local env_file="$FAA_DIR/.env"
  local env_example="$FAA_DIR/.env.example"
  if [[ -f "$env_file" ]]; then
    return 0
  fi
  if [[ -f "$env_example" ]]; then
    cp "$env_example" "$env_file"
    echo "Created $env_file from .env.example"
    return 0
  fi
  echo "Warning: FAA .env and .env.example are both missing in $FAA_DIR"
}

bootstrap_faa_postgis() {
  [[ "$FAA_POSTGIS_ENABLED" == "1" ]] || return 0
  if ! have_cmd docker; then
    echo "Warning: docker not found; skipping FAA PostGIS bootstrap."
    return 0
  fi

  local bootstrap_script="$FAA_DIR/scripts/bootstrap_postgis.sh"
  if [[ "$FAA_POSTGIS_PROFILE" == "source" ]]; then
    bootstrap_script="$FAA_DIR/scripts/bootstrap_postgis_source.sh"
  fi

  if [[ ! -f "$bootstrap_script" ]]; then
    echo "Warning: FAA PostGIS bootstrap script missing: $bootstrap_script"
    return 0
  fi

  echo "Bootstrapping FAA PostGIS (profile=$FAA_POSTGIS_PROFILE) ..."
  bash "$bootstrap_script"
}

install_hooks() {
  [[ "$INSTALL_CONDA_HOOKS" == "1" ]] || return 0
  if ! have_cmd conda; then
    echo "Warning: conda not found; skipping conda hook install."
    return 0
  fi
  if ! conda_env_exists; then
    echo "Warning: conda env '$CONDA_ENV_NAME' not found; skipping conda hook install."
    return 0
  fi
  echo "Installing conda activate/deactivate hooks ..."
  bash "$ROOT_DIR/scripts/install_conda_hooks.sh" "$CONDA_ENV_NAME"
}

setup_all() {
  sync_conda_env
  ensure_frontend_deps
  prepare_faa_env
  bootstrap_faa_postgis
  install_hooks
}

start_stack() {
  if [[ "$AUTO_START" != "1" ]]; then
    return 0
  fi
  if [[ "$START_DETACHED" == "1" ]]; then
    echo "Starting stack in detached mode ..."
    FAA_POSTGIS_ENABLED="$FAA_POSTGIS_ENABLED" START_MISSION_SUPERVISOR="${START_MISSION_SUPERVISOR:-1}" START_FRONTEND="${START_FRONTEND:-1}" \
      bash "$ROOT_DIR/scripts/conda_autostart_services.sh" start
  else
    echo "Starting stack in attached mode ..."
    exec env \
      "FAA_POSTGIS_ENABLED=$FAA_POSTGIS_ENABLED" \
      "START_MISSION_SUPERVISOR=${START_MISSION_SUPERVISOR:-1}" \
      "START_FRONTEND=${START_FRONTEND:-1}" \
      "$ROOT_DIR/run-dev.sh" restart
  fi
}

status_stack() {
  bash "$ROOT_DIR/scripts/conda_autostart_services.sh" status || true
  echo
  bash "$ROOT_DIR/run-dev.sh" ports || true
}

stop_stack() {
  bash "$ROOT_DIR/scripts/conda_autostart_services.sh" stop || true
  bash "$ROOT_DIR/run-dev.sh" stop || true
}

usage() {
  echo "Usage: $0 [once|setup|start|stop|restart|status]"
}

case "$ACTION" in
  once)
    setup_all
    start_stack
    ;;
  setup)
    setup_all
    ;;
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
    usage
    exit 1
    ;;
esac
