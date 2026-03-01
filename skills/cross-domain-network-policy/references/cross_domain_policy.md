# Cross-Domain Policy Notes

## Goal

Protect mission execution by enforcing network readiness gates tied to UTM context.

## Inputs

- Network KPIs from `/api/network/mission/state`.
- UTM context from `/api/utm/state`.
- MCP runtime profile from `/api/mcp/config`.

## Suggested Gates

- Coverage: `coverageScorePct >= 95`
- Latency: `avgLatencyMs <= 60`
- Interference: `highInterferenceRiskCount <= 1`

For video-heavy missions, tighten latency to `<= 45` and high-risk to `<= 0`.

## Decision Rules

- Any gate failure => `FAIL`.
- All gates pass => `PASS`.
- If MCP profile is unexpected for requested flow, mark as `FAIL` even if KPIs pass.
