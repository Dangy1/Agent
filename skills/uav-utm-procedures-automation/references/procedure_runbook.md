# Procedure Runbook

## Defaults

- `airspace_segment`: `sector-A3`
- `uav_id`: `uav-1`
- `user_id`: `user-1`
- `operator_license_id`: `op-001`
- `required_license_class`: `A1`

## Profile

Switch to procedures profile first:

```bash
./scripts/mcp_profile_preset.sh procedures
```

Expected active profile: `uav-utm-procedures-stdio`.

## Canonical Order

1. `prepare_operator_and_assign_uav`
2. `plan_route`
3. `submit_mission_auto`
4. `launch_and_step`
5. `status_snapshot`

## Conflict Recovery

On submit conflicts (NFZ/geofence/DSS conflict):

1. `replan_and_resubmit`
2. `status_snapshot`
3. If still blocked, stop and surface policy reason to operator.

## Evidence Checklist

- Active MCP profile from `/api/mcp/config`.
- Procedure tool call outcomes in order.
- `status_snapshot` payload after launch.
- Replan payload (if used).
