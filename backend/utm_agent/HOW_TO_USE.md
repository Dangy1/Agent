# How To Use This Platform With Your UAV

This guide is a practical, step-by-step workflow for operating a UAV in this codebase with:

- UAV API (flight simulation and mission actions)
- UTM API (route checks, approvals, compliance/security)
- USS/DSS-style operational intents and subscriptions
- Monitoring, conformance, and readiness checks

The examples below assume local dev ports from `run-dev.sh`:

- UAV API: `http://127.0.0.1:8020`
- UTM API: `http://127.0.0.1:8021`
- Network API: `http://127.0.0.1:8022`
- Frontend: `http://127.0.0.1:5173/#/uav` and `#/utm`

## 1. Start The Platform

From repo root:

```bash
./run-dev.sh start
```

Keep that terminal running.

## 2. Set API Variables

Open a second terminal:

```bash
export UAV_BASE="http://127.0.0.1:8020"
export UTM_BASE="http://127.0.0.1:8021"
export NET_BASE="http://127.0.0.1:8022"

# UTM API enforces bearer auth by default (except /api/utm/state, /api/utm/live/source, and /api/utm/layers/status).
export UTM_TOKEN="${UTM_TOKEN:-local-dev-token}"
export UTM_AUTH="Authorization: Bearer ${UTM_TOKEN}"
```

## 3. Health And Baseline Checks

```bash
curl -sS "${UAV_BASE}/api/uav/sim/state?uav_id=uav-1" | jq
curl -sS "${UTM_BASE}/api/utm/state?airspace_segment=sector-A3" | jq
curl -sS "${UTM_BASE}/api/utm/layers/status?airspace_segment=sector-A3" | jq
curl -sS "${UTM_BASE}/api/utm/security/status" -H "${UTM_AUTH}" | jq
```

## 4. Register Operator And UAV Context

Register operator license in UTM:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/license" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "operator_license_id":"op-001",
    "license_class":"VLOS",
    "uav_size_class":"middle",
    "expires_at":"2099-01-01T00:00:00Z",
    "active":true
  }' | jq
```

Assign UAV to user in UAV API:

```bash
curl -sS -X POST "${UAV_BASE}/api/uav/registry/assign" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"user-1",
    "uav_id":"uav-1",
    "operator_license_id":"op-001"
  }' | jq
```

## 5. Plan Route

```bash
curl -sS -X POST "${UAV_BASE}/api/uav/sim/plan" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"user-1",
    "uav_id":"uav-1",
    "route_id":"route-1",
    "waypoints":[
      {"x":10,"y":10,"z":20,"action":"TAKEOFF"},
      {"x":60,"y":70,"z":35},
      {"x":130,"y":120,"z":40},
      {"x":10,"y":10,"z":20,"action":"LAND"}
    ]
  }' | jq
```

## 6. Submit Mission (Auto UTM + DSS Workflow)

This is the easiest end-to-end preflight path. It orchestrates:

1. Route checks
2. Geofence submit
3. Verify-from-UAV
4. Approval request
5. DSS intent publication/conflict handling

```bash
curl -sS -X POST "${UAV_BASE}/api/uav/sim/utm-submit-mission" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"user-1",
    "uav_id":"uav-1",
    "airspace_segment":"sector-A3",
    "operator_license_id":"op-001",
    "required_license_class":"VLOS",
    "requested_speed_mps":12.0,
    "dss_conflict_policy":"reject"
  }' | jq
```

If `approved=false` or you see DSS conflict/error status, adjust route/NFZ/policy and submit again.

## 7. USS/DSS Operations (Manual Control Path)

Use this when you want direct DSS-style management.

Register your USS participant:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/dss/participants" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "participant_id":"uss-local-user-1",
    "uss_base_url":"http://127.0.0.1:9000",
    "roles":["uss"],
    "status":"active"
  }' | jq
```

Create a subscription:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/dss/subscriptions" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "subscription_id":"sub-user-1-a3",
    "manager_uss_id":"uss-local-user-1",
    "uss_base_url":"http://127.0.0.1:9000",
    "callback_url":"local://uss-local-user-1/callback",
    "notify_for":["create","update","delete"],
    "volume4d":{
      "x":[0,400],
      "y":[0,300],
      "z":[0,120],
      "time_start":"2026-02-27T12:00:00Z",
      "time_end":"2026-02-27T13:00:00Z"
    }
  }' | jq
```

Publish operational intent:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/dss/operational-intents" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "intent_id":"intent-uav-1-route-1",
    "manager_uss_id":"uss-local-user-1",
    "state":"accepted",
    "priority":"normal",
    "conflict_policy":"reject",
    "volume4d":{
      "x":[10,130],
      "y":[10,120],
      "z":[20,40],
      "time_start":"2026-02-27T12:05:00Z",
      "time_end":"2026-02-27T12:45:00Z"
    },
    "metadata":{"uav_id":"uav-1","route_id":"route-1"}
  }' | jq
```

Check DSS state and notifications:

```bash
curl -sS "${UTM_BASE}/api/utm/dss/state" -H "${UTM_AUTH}" | jq
curl -sS "${UTM_BASE}/api/utm/dss/notifications?status=pending&limit=20" -H "${UTM_AUTH}" | jq
curl -sS "${UTM_BASE}/api/utm/layers/status?airspace_segment=sector-A3" | jq
```

Ack a notification:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/dss/notifications/<notification_id>/ack" \
  -H "${UTM_AUTH}" | jq
```

Manual dispatch cycle (debug):

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/dss/notifications/dispatch" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{"run_limit":1}' | jq
```

Check dispatch worker status/config + last cycle stats:

```bash
curl -sS "${UTM_BASE}/api/utm/dss/notifications/dispatch/status" \
  -H "${UTM_AUTH}" | jq
```

Optional: enable background callback delivery for `http(s)` subscription callback URLs:

```bash
export UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED=true
export UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S=1.0
export UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S=3.0
export UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE=20
export UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS=8
```

When enabled, pending notifications with `http://` or `https://` `callback_url` are POSTed in the background and transition to `delivered` (or `failed` after max attempts). Non-HTTP callback URLs remain queue-only.
`run-dev.sh` now defaults this dispatcher to enabled (`UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED=1`); set it to `0` to disable.

## 8. Launch And Fly

Launch:

```bash
curl -sS -X POST "${UAV_BASE}/api/uav/sim/launch?uav_id=uav-1&user_id=user-1" | jq
```

Step mission:

```bash
curl -sS -X POST "${UAV_BASE}/api/uav/sim/step" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","uav_id":"uav-1","ticks":5}' | jq
```

Hold / Resume / RTH / Land:

```bash
curl -sS -X POST "${UAV_BASE}/api/uav/sim/hold" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","uav_id":"uav-1","reason":"traffic deconfliction"}' | jq

curl -sS -X POST "${UAV_BASE}/api/uav/sim/resume?uav_id=uav-1&user_id=user-1" | jq
curl -sS -X POST "${UAV_BASE}/api/uav/sim/rth?uav_id=uav-1&user_id=user-1" | jq
curl -sS -X POST "${UAV_BASE}/api/uav/sim/land?uav_id=uav-1&user_id=user-1" | jq
```

## 9. Live Monitoring

UAV state/fleet:

```bash
curl -sS "${UAV_BASE}/api/uav/sim/state?uav_id=uav-1" | jq
curl -sS "${UAV_BASE}/api/uav/sim/fleet" | jq
```

UTM + DSS state:

```bash
curl -sS "${UTM_BASE}/api/utm/state?airspace_segment=sector-A3&operator_license_id=op-001" | jq
curl -sS "${UTM_BASE}/api/utm/dss/state" -H "${UTM_AUTH}" | jq
curl -sS "${UTM_BASE}/api/utm/sync?limit_actions=20" -H "${UTM_AUTH}" | jq
```

Network mission view:

```bash
curl -sS "${NET_BASE}/api/network/mission/state?airspace_segment=sector-A3&selected_uav_id=uav-1" | jq
```

## 10. Conformance, Operations Readiness, And Resilience

Run DSS local conformance scenarios:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/conformance/run-local" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{"reset_before_run":true}' | jq

curl -sS "${UTM_BASE}/api/utm/conformance/last" -H "${UTM_AUTH}" | jq
```

Evaluate ops readiness:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/operations/readiness/evaluate" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "jurisdiction_profile":"us_faa_ntap",
    "observed_metrics":{"uptime_pct":99.95,"incident_response_sla_ok":true}
  }' | jq
```

Run resilience campaign:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/resilience/campaigns/start" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "campaign_id":"res-camp-1",
    "name":"monthly-resilience",
    "release_id":"release-local",
    "cadence_days":30,
    "created_by":"ops"
  }' | jq

curl -sS -X POST "${UTM_BASE}/api/utm/resilience/campaigns/res-camp-1/run" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{"executed_by":"ops","fault_profile":"baseline"}' | jq

curl -sS "${UTM_BASE}/api/utm/resilience/campaigns/res-camp-1/summary" -H "${UTM_AUTH}" | jq
```

## 11. Compliance And Certification Artifacts

Export compliance package:

```bash
curl -sS "${UTM_BASE}/api/utm/compliance/export?limit_actions=100&include_action_payloads=false&include_rtm_text=false" \
  -H "${UTM_AUTH}" | jq
```

Generate certification pack:

```bash
curl -sS -X POST "${UTM_BASE}/api/utm/certification/pack/generate" \
  -H "${UTM_AUTH}" -H "Content-Type: application/json" \
  -d '{
    "jurisdiction_profile":"us_faa_ntap",
    "release_id":"release-local",
    "candidate_version":"0.1.0"
  }' | jq
```

## 12. Optional: Mission Supervisor API

If you want cross-domain orchestration API endpoints (`/api/mission/*`), start separately:

```bash
cd backend
uvicorn mission_supervisor_agent.api:app --host 0.0.0.0 --port 8023 --reload
```

Then:

```bash
curl -sS -X POST "http://127.0.0.1:8023/api/mission/start" \
  -H "Content-Type: application/json" \
  -d '{"request_text":"Fly uav-1 from point A to B with UTM approval","mission_id":"mission-1"}' | jq
```

## 13. Cleanup (After Mission)

Delete intent/subscription when done:

```bash
curl -sS -X DELETE "${UTM_BASE}/api/utm/dss/operational-intents/intent-uav-1-route-1" -H "${UTM_AUTH}" | jq
curl -sS -X DELETE "${UTM_BASE}/api/utm/dss/subscriptions/sub-user-1-a3" -H "${UTM_AUTH}" | jq
```

Stop stack:

```bash
./run-dev.sh stop
```

## Notes

- If you receive `401 missing_bearer_token` or `403 insufficient_role`, check `UTM_TOKEN` and roles via `/api/utm/security/status`.
- `dss_conflict_policy` supports `reject`, `negotiate`, `conditional_approve`.
- UAV launch is strictly gate-protected by UTM approval + DSS/subscription safety checks.
- Resume may be blocked by DSS strategic conflicts/degraded status; tactical controls (`step`, `hold`, `rth`, `land`) use UAV state checks.
- Use `backend/uav_agent/UAV_AGENT_WORKFLOW.md` for deeper UAV internals.
