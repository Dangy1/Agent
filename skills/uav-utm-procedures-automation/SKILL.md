---
name: uav-utm-procedures-automation
description: Automate and troubleshoot high-level UAV+UTM mission procedures using the local MCP procedures profile and APIs. Use when requests mention prepare/assign UAV, route planning, UTM submission, launch/step, replan-after-conflict, or end-to-end mission evidence for the procedures workflow.
---

# UAV UTM Procedures Automation

## Overview
Run the procedure-first UAV/UTM flow through the local stack with deterministic checks and evidence. Prefer this skill when the task is mission execution, not low-level DSS/security administration.

## Workflow

1. Verify stack reachability and active profile.
2. Switch to `uav-utm-procedures-stdio` before procedure calls.
3. Execute the recommended operation order.
4. Capture status evidence and protocol trace links for the final report.

## Quick Start
From repo root:

```bash
./scripts/mcp_profile_preset.sh procedures
./skills/uav-utm-procedures-automation/scripts/procedure_preflight.sh
```

If preflight passes, run procedure operations in this order:

1. `prepare_operator_and_assign_uav`
2. `plan_route`
3. `submit_mission_auto`
4. `launch_and_step`
5. `status_snapshot`

## Failure Handling
If mission submission is blocked by NFZ/geofence/conflict responses:

1. Run `replan_and_resubmit`.
2. Re-run `status_snapshot`.
3. Report both the original failure payload and replan payload.

If profile/config is wrong:

1. Re-apply profile with `./scripts/mcp_profile_preset.sh procedures`.
2. Confirm `active_profile` via `/api/mcp/config`.

## Reporting Contract
Always include:

1. Active MCP profile.
2. API preflight result (`8010`, `8020`, `8021`, `8022`, `8023`).
3. Mission identifiers (`uav_id`, `route_id`, `operator_license_id`, `airspace_segment`).
4. Submit outcome and launch/step outcome.
5. Any replan action taken.

## References
Load these files when needed:

- `references/procedure_runbook.md`: defaults, sequence, and evidence checklist.
- `backend/others/MCP_UAV_UTM_PROCEDURES.md`: tool inventory and environment options.
- `README.md`: stack ports and smoke commands.
