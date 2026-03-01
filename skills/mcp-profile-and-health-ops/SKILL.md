---
name: mcp-profile-and-health-ops
description: Switch MCP runtime profiles and run cross-service health validation for the local stack. Use when requests involve MCP profile/preset changes, transport config validation, stack smoke checks, or diagnosing broken profile wiring across O-RAN, UAV, UTM, network, and mission services.
---

# MCP Profile And Health Ops

## Overview
Manage MCP runtime mode safely and verify service readiness across all local APIs. Use this skill before and after profile changes to prevent silent misconfiguration.

## Workflow

1. Capture current profile/config snapshot.
2. Apply a profile or preset.
3. Verify active profile matches intent.
4. Run health checks for all core services.

## Quick Start
From repo root:

```bash
./skills/mcp-profile-and-health-ops/scripts/profile_health_check.sh --preset procedures
./skills/mcp-profile-and-health-ops/scripts/profile_health_check.sh --preset strict-ops
```

To target a profile directly:

```bash
./skills/mcp-profile-and-health-ops/scripts/profile_health_check.sh --profile uav-utm-procedures-stdio
```

## Supported Targets

- Presets: `procedures`, `strict-ops`, `show`
- Profiles:
1. `suites-stdio`
2. `suites-http`
3. `uav-utm-procedures-stdio`
4. `uav-utm-strict-ops-stdio`

## Reporting Contract
Always report:

1. Selected profile/preset.
2. Final `active_profile` from `/api/mcp/config`.
3. Per-service PASS/FAIL for `2024`, `8010`, `8020`, `8021`, `8022`, `8023`, `8024`, `8025`.

## References

- `references/profile_matrix.md`: profile list and endpoint matrix.
- `backend/oran_agent/config/runtime_mcp.py`: authoritative profile mapping.
- `README.md`: stack smoke commands.
