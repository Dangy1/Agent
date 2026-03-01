#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-show}"
ORAN_MCP_API_PORT="${ORAN_MCP_API_PORT:-8010}"
MCP_CONFIG_BASE_URL="${MCP_CONFIG_BASE_URL:-http://127.0.0.1:${ORAN_MCP_API_PORT}}"
PRESETS_ENDPOINT="${MCP_CONFIG_BASE_URL}/api/mcp/presets"

print_json() {
  if command -v jq >/dev/null 2>&1; then
    jq
  else
    cat
  fi
}

normalize_action() {
  local raw="$1"
  case "${raw}" in
    procedures|procedure|uav-utm-procedures)
      echo "procedures"
      ;;
    strict-ops|strict_ops|strictops|uav-utm-strict-ops)
      echo "strict-ops"
      ;;
    show|status|list)
      echo "show"
      ;;
    *)
      echo "${raw}"
      ;;
  esac
}

usage() {
  cat <<'EOF'
Usage:
  scripts/mcp_profile_preset.sh show
  scripts/mcp_profile_preset.sh procedures
  scripts/mcp_profile_preset.sh strict-ops

Environment:
  ORAN_MCP_API_PORT   (default: 8010)
  MCP_CONFIG_BASE_URL (default: http://127.0.0.1:${ORAN_MCP_API_PORT})
EOF
}

action="$(normalize_action "${ACTION}")"

case "${action}" in
  show)
    curl -sS "${PRESETS_ENDPOINT}" | print_json
    ;;
  procedures|strict-ops)
    curl -sS -X POST "${MCP_CONFIG_BASE_URL}/api/mcp/preset/${action}" \
      -H "Content-Type: application/json" \
      | print_json
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    usage
    exit 1
    ;;
esac
