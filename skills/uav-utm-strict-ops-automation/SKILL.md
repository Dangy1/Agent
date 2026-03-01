---
name: uav-utm-strict-ops-automation
description: Run strict UTM operations focused on DSS, conformance, and security flows through the strict-ops MCP profile. Use when tasks require DSS participant/subscription/intent operations, conformance checks, compliance export, trust-store/key rotation, or strict operational evidence.
---

# UAV UTM Strict Ops Automation

## Overview
Execute low-level UTM operations with strict boundaries across DSS, conformance, and security. Use this skill when procedure-level mission tools are not enough and policy/compliance controls must be validated directly.

## Workflow

1. Switch to strict-ops profile.
2. Run read-only state checks.
3. Apply required DSS/conformance/security action.
4. Re-check state and capture evidence.

## Quick Start
From repo root:

```bash
./scripts/mcp_profile_preset.sh strict-ops
./skills/uav-utm-strict-ops-automation/scripts/strict_ops_smoke.sh
```

## DSS Flow
Preferred order for DSS setup and cleanup:

1. `dss_upsert_participant`
2. `dss_upsert_subscription`
3. `dss_upsert_operational_intent`
4. `dss_query_notifications`
5. `dss_ack_notification` (if pending)
6. Cleanup with `dss_delete_operational_intent` and `dss_delete_subscription`

## Conformance and Security Flow

1. Run `conformance_run_local`, then check `conformance_last`.
2. Export evidence with `compliance_export`.
3. Verify security posture with `security_status` and `security_trust_store`.
4. Apply token/key controls only when requested.

## Guardrails

- Avoid destructive DSS deletes unless explicitly requested.
- Keep strict-op changes scoped to known test IDs.
- Always return the before/after state snapshot in your report.

## References

- `references/strict_ops_checklist.md`: deterministic sequence and cleanup IDs.
- `backend/others/MCP_UAV_UTM_STRICT_OPS.md`: strict-op tool groups.
- `backend/utm_agent/api.py`: DSS/conformance/security HTTP endpoints.
