#!/usr/bin/env bash
set -euo pipefail

PROFILE=""
PRESET=""
HOST="${HOST:-127.0.0.1}"
TOKEN="${UTM_SERVICE_TOKEN:-local-dev-token}"
TIMEOUT_S="${TIMEOUT_S:-5}"

usage() {
  cat <<'USAGE'
Usage: profile_health_check.sh [--profile <profile>] [--preset <preset>]

Examples:
  profile_health_check.sh --preset procedures
  profile_health_check.sh --profile uav-utm-strict-ops-stdio

Environment overrides:
  HOST=127.0.0.1
  UTM_SERVICE_TOKEN=local-dev-token
  TIMEOUT_S=5
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --preset)
      PRESET="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -n "$PROFILE" && -n "$PRESET" ]]; then
  echo "Use either --profile or --preset, not both." >&2
  exit 2
fi

if [[ -n "$PRESET" ]]; then
  if [[ -x "./scripts/mcp_profile_preset.sh" ]]; then
    ./scripts/mcp_profile_preset.sh "$PRESET" >/dev/null
  else
    curl -sS --max-time "$TIMEOUT_S" -X POST "http://${HOST}:8010/api/mcp/preset/${PRESET}" >/dev/null
  fi
fi

if [[ -n "$PROFILE" ]]; then
  curl -sS --max-time "$TIMEOUT_S" -X POST "http://${HOST}:8010/api/mcp/profile" \
    -H "Content-Type: application/json" \
    -d "{\"profile\":\"${PROFILE}\"}" >/dev/null
fi

cfg="$(curl -sS --max-time "$TIMEOUT_S" "http://${HOST}:8010/api/mcp/config")"
active_profile="$(printf '%s' "$cfg" | python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
    print(data.get("active_profile") or "")
except Exception:
    print("")
' 2>/dev/null || true)"

echo "INFO active_profile=${active_profile:-<none>}"

if [[ -n "$PROFILE" && "$active_profile" != "$PROFILE" ]]; then
  echo "FAIL active profile mismatch: expected $PROFILE got ${active_profile:-<none>}"
  exit 1
fi

failures=0

check_json_success() {
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

check_http_head() {
  local name="$1"
  local url="$2"
  if curl -sS --max-time "$TIMEOUT_S" -I "$url" >/dev/null; then
    echo "PASS $name"
  else
    echo "FAIL $name (request failed)"
    failures=$((failures + 1))
  fi
}

check_json_success "LangGraph openapi" "http://${HOST}:2024/openapi.json"
check_json_success "MCP config" "http://${HOST}:8010/api/mcp/config"
check_json_success "UAV fleet" "http://${HOST}:8020/api/uav/sim/fleet"
check_json_success "UTM sync" "http://${HOST}:8021/api/utm/sync" "Authorization: Bearer ${TOKEN}"
check_json_success "Network mission state" "http://${HOST}:8022/api/network/mission/state?airspace_segment=sector-A3&selected_uav_id=uav-1"
check_json_success "Mission list" "http://${HOST}:8023/api/mission"
check_json_success "DSS state" "http://${HOST}:8024/api/dss/state"
check_json_success "USS state" "http://${HOST}:8025/api/uss/state"
check_http_head "Frontend" "http://${HOST}:5173"

if (( failures > 0 )); then
  echo "Health check failed (${failures} checks)."
  exit 1
fi

echo "Health check passed."
