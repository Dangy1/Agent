#!/usr/bin/env bash
set -euo pipefail

MODE="balanced"
COVERAGE_TARGET="95"
MAX_LATENCY_MS="60"
MAX_HIGH_RISK="1"
AIRSPACE_SEGMENT="sector-A3"
UAV_ID="uav-1"
HOST="${HOST:-127.0.0.1}"
TIMEOUT_S="${TIMEOUT_S:-6}"

usage() {
  cat <<'USAGE'
Usage: network_policy_guard.sh [options]

Options:
  --mode <balanced|coverage|qos>
  --coverage-target <float>
  --max-latency-ms <float>
  --max-high-risk <int>
  --airspace-segment <id>
  --uav-id <id>

Environment overrides:
  HOST=127.0.0.1
  TIMEOUT_S=6
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-balanced}"
      shift 2
      ;;
    --coverage-target)
      COVERAGE_TARGET="${2:-95}"
      shift 2
      ;;
    --max-latency-ms)
      MAX_LATENCY_MS="${2:-60}"
      shift 2
      ;;
    --max-high-risk)
      MAX_HIGH_RISK="${2:-1}"
      shift 2
      ;;
    --airspace-segment)
      AIRSPACE_SEGMENT="${2:-sector-A3}"
      shift 2
      ;;
    --uav-id)
      UAV_ID="${2:-uav-1}"
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

optimize_payload="$(printf '{"mode":"%s","coverage_target_pct":%s,"max_tx_cap_dbm":41.0,"qos_priority_weight":68.0}' "$MODE" "$COVERAGE_TARGET")"

curl -sS --max-time "$TIMEOUT_S" -X POST "http://${HOST}:8022/api/network/optimize" \
  -H "Content-Type: application/json" \
  -d "$optimize_payload" >/tmp/network_optimize_guard.json

curl -sS --max-time "$TIMEOUT_S" "http://${HOST}:8022/api/network/mission/state?airspace_segment=${AIRSPACE_SEGMENT}&selected_uav_id=${UAV_ID}" >/tmp/network_state_guard.json
curl -sS --max-time "$TIMEOUT_S" "http://${HOST}:8021/api/utm/state?airspace_segment=${AIRSPACE_SEGMENT}" >/tmp/utm_state_guard.json
curl -sS --max-time "$TIMEOUT_S" "http://${HOST}:8010/api/mcp/config" >/tmp/mcp_config_guard.json

python3 - "$COVERAGE_TARGET" "$MAX_LATENCY_MS" "$MAX_HIGH_RISK" <<'PY'
import json
import sys

coverage_target = float(sys.argv[1])
max_latency = float(sys.argv[2])
max_high_risk = int(sys.argv[3])

with open('/tmp/network_optimize_guard.json', 'r', encoding='utf-8') as f:
    optimize = json.load(f)
with open('/tmp/network_state_guard.json', 'r', encoding='utf-8') as f:
    network = json.load(f)
with open('/tmp/utm_state_guard.json', 'r', encoding='utf-8') as f:
    utm = json.load(f)
with open('/tmp/mcp_config_guard.json', 'r', encoding='utf-8') as f:
    mcp = json.load(f)

if str(network.get('status', '')).lower() != 'success':
    print('FAIL network state request failed')
    raise SystemExit(1)

if str(utm.get('status', '')).lower() != 'success':
    print('FAIL utm state request failed')
    raise SystemExit(1)

kpis = (((network.get('result') or {}).get('networkKpis')) or {})
coverage = float(kpis.get('coverageScorePct', 0.0) or 0.0)
latency = float(kpis.get('avgLatencyMs', 9999.0) or 9999.0)
high_risk = int(kpis.get('highInterferenceRiskCount', 9999) or 9999)
active_profile = mcp.get('active_profile')

print(f"INFO optimize_status={optimize.get('status')}")
print(f"INFO active_profile={active_profile}")
print(f"INFO coverageScorePct={coverage}")
print(f"INFO avgLatencyMs={latency}")
print(f"INFO highInterferenceRiskCount={high_risk}")

failed = False
if coverage < coverage_target:
    print(f"FAIL coverageScorePct {coverage} < target {coverage_target}")
    failed = True
if latency > max_latency:
    print(f"FAIL avgLatencyMs {latency} > max {max_latency}")
    failed = True
if high_risk > max_high_risk:
    print(f"FAIL highInterferenceRiskCount {high_risk} > max {max_high_risk}")
    failed = True

if failed:
    print('DECISION FAIL')
    raise SystemExit(1)

print('DECISION PASS')
PY
