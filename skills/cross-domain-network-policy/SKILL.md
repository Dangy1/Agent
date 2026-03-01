---
name: cross-domain-network-policy
description: Coordinate UAV mission constraints with network KPI policy and MCP/O-RAN profile checks across UTM, network, and mission services. Use when tasks ask for cross-domain assurance, network policy gates before launch, or combined UAV+UTM+network readiness decisions.
---

# Cross Domain Network Policy

## Overview
Apply policy-style readiness checks that combine network KPIs, UTM state, and MCP profile context before mission execution. Use this skill to enforce explicit go/no-go thresholds.

## Workflow

1. Snapshot MCP profile, network state, and UTM state.
2. Apply network optimization target if requested.
3. Evaluate coverage/latency/interference thresholds.
4. Return pass/fail decision with domain evidence.

## Quick Start
From repo root:

```bash
./skills/cross-domain-network-policy/scripts/network_policy_guard.sh \
  --mode balanced \
  --coverage-target 95 \
  --max-latency-ms 60 \
  --max-high-risk 1
```

## Default Policy Gates

1. `coverageScorePct >= 95`
2. `avgLatencyMs <= 60`
3. `highInterferenceRiskCount <= 1`

Override gates only when user specifies different requirements.

## Reporting Contract
Always include:

1. Selected optimization mode and payload.
2. Network KPI values vs thresholds.
3. UTM weather/license/dss summary context.
4. Active MCP profile.
5. Final decision: `PASS` or `FAIL`.

## References

- `references/cross_domain_policy.md`: policy examples and interpretation.
- `backend/network_agent/service.py`: authoritative KPI field names.
- `backend/utm_agent/api.py`: UTM state structure.
- `backend/oran_agent/api.py`: MCP config endpoint.
