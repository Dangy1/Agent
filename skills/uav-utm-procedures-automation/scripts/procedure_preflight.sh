#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
TOKEN="${UTM_SERVICE_TOKEN:-local-dev-token}"
AIRSPACE_SEGMENT="${AIRSPACE_SEGMENT:-sector-A3}"
UAV_ID="${UAV_ID:-uav-1}"
TIMEOUT_S="${TIMEOUT_S:-4}"

if [[ "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: procedure_preflight.sh

Environment overrides:
  HOST=127.0.0.1
  UTM_SERVICE_TOKEN=local-dev-token
  AIRSPACE_SEGMENT=sector-A3
  UAV_ID=uav-1
  TIMEOUT_S=4
USAGE
  exit 0
fi

failures=0

check_success_json() {
  local name="$1"
  local url="$2"
  local auth_header="${3:-}"

  local body
  if [[ -n "$auth_header" ]]; then
    if ! body="$(curl -sS --max-time "$TIMEOUT_S" -H "$auth_header" "$url")"; then
      echo "FAIL $name (request failed)"
      failures=$((failures + 1))
      return
    fi
  else
    if ! body="$(curl -sS --max-time "$TIMEOUT_S" "$url")"; then
      echo "FAIL $name (request failed)"
      failures=$((failures + 1))
      return
    fi
  fi

  if echo "$body" | tr -d '\n' | grep -Eqi '"status"[[:space:]]*:[[:space:]]*"success"'; then
    echo "PASS $name"
  else
    echo "FAIL $name (status!=success)"
    failures=$((failures + 1))
  fi
}

check_success_json "MCP config" "http://${HOST}:8010/api/mcp/config"
check_success_json "UAV fleet" "http://${HOST}:8020/api/uav/sim/fleet"
check_success_json "UTM sync" "http://${HOST}:8021/api/utm/sync" "Authorization: Bearer ${TOKEN}"
check_success_json "Network mission state" "http://${HOST}:8022/api/network/mission/state?airspace_segment=${AIRSPACE_SEGMENT}&selected_uav_id=${UAV_ID}"
check_success_json "Mission skills" "http://${HOST}:8023/api/mission/skills"

active_profile="$({ curl -sS --max-time "$TIMEOUT_S" "http://${HOST}:8010/api/mcp/config" || true; } | python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
    print(data.get("active_profile") or "")
except Exception:
    print("")
' 2>/dev/null || true)"

if [[ -n "$active_profile" ]]; then
  echo "INFO active_profile=${active_profile}"
fi

if (( failures > 0 )); then
  echo "Preflight failed (${failures} checks)."
  exit 1
fi

echo "Preflight passed."
