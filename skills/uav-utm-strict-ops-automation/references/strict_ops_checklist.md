# Strict Ops Checklist

## Profile

```bash
./scripts/mcp_profile_preset.sh strict-ops
```

Expected active profile: `uav-utm-strict-ops-stdio`.

## DSS Baseline Sequence

1. `dss_state`
2. `dss_upsert_participant` (test participant)
3. `dss_upsert_subscription` (test subscription)
4. `dss_query_subscriptions`
5. `dss_upsert_operational_intent`
6. `dss_query_operational_intents`
7. `dss_query_notifications`
8. Optional `dss_ack_notification`
9. Cleanup: delete intent + subscription

## Conformance

1. `conformance_run_local`
2. `conformance_last`
3. `compliance_export`

## Security

1. `security_status`
2. `security_trust_store`
3. Optional key/token operations only on explicit request.

## Evidence

- Include at least one DSS state snapshot before and after writes.
- Include conformance summary and compliance export location.
- Include security status and trust-store count.
