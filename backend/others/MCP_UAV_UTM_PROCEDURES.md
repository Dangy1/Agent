# UAV/UTM Procedure MCP Server

This MCP server provides high-level, procedure-first tools for UAV + UTM workflows.

## File

- `backend/others/mcp_uav_utm_procedures.py`

## Start

From repo root:

```bash
python3 backend/others/mcp_uav_utm_procedures.py
```

It runs as a stdio MCP server (`FastMCP`).

Requirement: Python environment must have the `mcp` package installed.

## Environment

Optional overrides:

```bash
export UAV_BASE_URL="http://127.0.0.1:8020"
export UTM_BASE_URL="http://127.0.0.1:8021"
export NETWORK_BASE_URL="http://127.0.0.1:8022"
export UTM_SERVICE_TOKEN="local-dev-token"
export MCP_UAV_UTM_TIMEOUT_S="6.0"
```

## Tools

- `tool_overview`
- `health_check`
- `status_snapshot`
- `prepare_operator_and_assign_uav`
- `plan_route`
- `submit_mission_auto`
- `launch_and_step`
- `control_action`
- `replan_and_resubmit`
- `setup_dss_subscription`
- `run_standard_procedure`

## Recommended Procedure

1. `prepare_operator_and_assign_uav`
2. `plan_route`
3. `submit_mission_auto`
4. `launch_and_step`
5. `status_snapshot`

Use `replan_and_resubmit` when UTM submit fails due to NFZ/geofence conflicts.

## Runtime Profile Wiring

These profiles are now available in MCP runtime config API:

- `uav-utm-procedures-stdio`
- `uav-utm-strict-ops-stdio`

Switch to procedures profile:

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/mcp/profile" \
  -H "Content-Type: application/json" \
  -d '{"profile":"uav-utm-procedures-stdio"}' | jq
```

Switch to strict-ops profile:

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/mcp/profile" \
  -H "Content-Type: application/json" \
  -d '{"profile":"uav-utm-strict-ops-stdio"}' | jq
```

Quick one-command switch:

```bash
./scripts/mcp_profile_preset.sh procedures
./scripts/mcp_profile_preset.sh strict-ops
./scripts/mcp_profile_preset.sh show
```
