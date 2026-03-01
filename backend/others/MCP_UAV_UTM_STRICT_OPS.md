# UAV/UTM Strict Ops MCP Server

This MCP server provides strict UTM operation tools grouped by:

- DSS-only flows
- Conformance-only flows
- Security-only flows

## File

- `backend/others/mcp_uav_utm_strict_ops.py`

## Start

From repo root:

```bash
python3 backend/others/mcp_uav_utm_strict_ops.py
```

It runs as a stdio MCP server (`FastMCP`).

Requirement: Python environment must have the `mcp` package installed.

## Environment

Optional overrides:

```bash
export UTM_BASE_URL="http://127.0.0.1:8021"
export UTM_SERVICE_TOKEN="local-dev-token"
export MCP_UAV_UTM_TIMEOUT_S="6.0"
```

## Tool Groups

### DSS

- `dss_state`
- `dss_upsert_participant`
- `dss_upsert_subscription`
- `dss_query_subscriptions`
- `dss_upsert_operational_intent`
- `dss_query_operational_intents`
- `dss_delete_operational_intent`
- `dss_delete_subscription`
- `dss_query_notifications`
- `dss_ack_notification`
- `dss_dispatch_notifications`

### Conformance

- `conformance_run_local`
- `conformance_last`
- `compliance_export`

### Security

- `security_status`
- `security_trust_store`
- `security_register_peer_key`
- `security_rotate_key`
- `security_upsert_service_token`
- `security_get_key_rotation_policy`
- `security_set_key_rotation_policy`

## Runtime Profile Wiring

Use MCP runtime profile:

- `uav-utm-strict-ops-stdio`

```bash
curl -sS -X POST "http://127.0.0.1:8010/api/mcp/profile" \
  -H "Content-Type: application/json" \
  -d '{"profile":"uav-utm-strict-ops-stdio"}' | jq
```

Quick one-command switch:

```bash
./scripts/mcp_profile_preset.sh strict-ops
./scripts/mcp_profile_preset.sh procedures
./scripts/mcp_profile_preset.sh show
```
