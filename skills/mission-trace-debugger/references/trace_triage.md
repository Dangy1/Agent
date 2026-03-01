# Mission Trace Triage

## Triage Order

1. Read mission state (`/api/mission/<id>/state`) and note terminal status.
2. Read protocol trace with replayed rows included.
3. Re-run trace excluding replayed rows for causal chain clarity.
4. Group failures by `domain`, `op`, and `status`.

## Common Patterns

- `utm` + conflict/geofence indicators: route to replan and resubmit.
- `network` + high interference/latency: optimize network before rerun.
- `oran` or MCP bridge failures: validate active profile and MCP config.
- Replayed-heavy traces: inspect first non-replayed failure as root trigger.

## Required Output Fields

- Mission ID and status.
- Trace total, replayed count.
- Top failing domain/op pairs.
- First failing command IDs with correlation IDs.
- Recommended next action per failing domain.
