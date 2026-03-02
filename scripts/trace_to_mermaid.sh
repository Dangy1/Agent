#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "" ]]; then
  echo "Usage: $0 <mission_id> [output_file] [api_base_url]" >&2
  echo "Example: $0 mission-abc123 /tmp/mission-abc123.mmd http://127.0.0.1:8023" >&2
  exit 1
fi

MISSION_ID="$1"
OUTPUT_FILE="${2:-/tmp/${MISSION_ID}_protocol_trace.mmd}"
API_BASE="${3:-${MISSION_API_BASE:-http://127.0.0.1:8023}}"
TRACE_LIMIT="${TRACE_LIMIT:-500}"
INCLUDE_REPLAYED="${INCLUDE_REPLAYED:-true}"

if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl is required" >&2
  exit 1
fi

API_BASE="${API_BASE%/}"
URL="${API_BASE}/api/mission/${MISSION_ID}/protocol-trace/mermaid?limit=${TRACE_LIMIT}&include_replayed=${INCLUDE_REPLAYED}"

mkdir -p "$(dirname "$OUTPUT_FILE")"
curl -fsS "$URL" -o "$OUTPUT_FILE"

echo "Wrote Mermaid trace: $OUTPUT_FILE"
echo "Mission: $MISSION_ID"
echo "URL: $URL"
