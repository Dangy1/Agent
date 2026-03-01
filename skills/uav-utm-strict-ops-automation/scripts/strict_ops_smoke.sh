#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
TOKEN="${UTM_SERVICE_TOKEN:-local-dev-token}"
TIMEOUT_S="${TIMEOUT_S:-5}"

if [[ "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: strict_ops_smoke.sh

Environment overrides:
  HOST=127.0.0.1
  UTM_SERVICE_TOKEN=local-dev-token
  TIMEOUT_S=5
USAGE
  exit 0
fi

failures=0

check_auth_json_success() {
  local name="$1"
  local url="$2"
  local body

  if ! body="$(curl -sS --max-time "$TIMEOUT_S" -H "Authorization: Bearer ${TOKEN}" "$url")"; then
    echo "FAIL $name (request failed)"
    failures=$((failures + 1))
    return
  fi

  if echo "$body" | tr -d '\n' | grep -Eqi '"status"[[:space:]]*:[[:space:]]*"success"'; then
    echo "PASS $name"
  else
    echo "FAIL $name (status!=success)"
    failures=$((failures + 1))
  fi
}

check_auth_json_success "DSS state" "http://${HOST}:8021/api/utm/dss/state"
check_auth_json_success "Conformance last" "http://${HOST}:8021/api/utm/conformance/last"
check_auth_json_success "Security status" "http://${HOST}:8021/api/utm/security/status"
check_auth_json_success "Trust store" "http://${HOST}:8021/api/utm/security/trust-store"
check_auth_json_success "Compliance export" "http://${HOST}:8021/api/utm/compliance/export"

if (( failures > 0 )); then
  echo "Strict ops smoke failed (${failures} checks)."
  exit 1
fi

echo "Strict ops smoke passed."
