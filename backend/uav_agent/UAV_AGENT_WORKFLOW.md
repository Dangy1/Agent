# UAV Agent Workflow And Cross-Agent Interaction

This document explains how the UAV agent works today, how mission flow moves through the system, and how it interacts with UTM, Network, and Mission Supervisor agents.

## 1) Scope

The UAV agent is responsible for:

1. Managing UAV simulator state (route, phase, position, mission controls).
2. Managing user-to-UAV assignment and advanced UAV profiles.
3. Running route safety flow with UTM checks and approvals.
4. Supporting AI copilot-driven route replan and network optimization.
5. Persisting mission state, path history, approvals, and audit actions.

It does not replace the mission supervisor. Mission supervisor orchestrates cross-domain missions and delegates operations to UAV/UTM/Network tools.

## 2) Main Runtime Components

### UAV domain

1. `uav_agent.api`  
   FastAPI assembly that mounts:
   - `api_routes_uav.py`
   - `api_routes_utm.py`
   - `api_routes_network.py`
2. `uav_agent.simulator.SIM`  
   In-memory simulator for UAV fleet state and flight progression.
3. `uav_agent.tools`  
   Tool wrappers for plan, geofence submit, approval request, launch, step, hold, resume, RTH, land, and NFZ-aware replan.
4. `uav_agent.copilot_workflow`  
   LangGraph workflow used by `/api/uav/agent/chat`.

### Shared services used by UAV API

1. `utm_agent.service.UTM_SERVICE`  
   Performs route bounds, weather, NFZ, regulation, time window, and license checks; issues approvals.
2. `network_agent.service.NETWORK_MISSION_SERVICE`  
   Provides mission network state and network optimization controls.
3. `agent_db.AgentDB("uav")` and `AgentDB("utm")`  
   Persists snapshot state and action logs.

### Mission supervisor side

1. `mission_supervisor_agent.graph`  
   End-to-end orchestration graph (ingest -> risk -> watchers -> planning -> policy -> dispatch -> verify -> progress/recovery).
2. `mission_supervisor_agent.domain_dispatch`  
   Delegates plan steps to UAV/UTM/Network tools.

## 3) Persistence Model (UAV DB)

UAV API writes major state into `AgentDB("uav")`:

1. `fleet`  
   Full simulator fleet snapshot.
2. `uav_registry`  
   - `users`: `user_id -> {uav_ids, updated_at}`
   - `uavs`: `uav_id -> {owner_user_id, operator_license_id, standardized_profile, updated_at}`
3. `uav_mission_defaults`  
   Per `user_id:uav_id` default mission inputs.
4. `uav_utm_sessions`  
   Per `user_id:uav_id` latest UTM approval + geofence result used for flight control gating.
5. `planned_route_history`  
   Latest path records by category (`user_planned`, `agent_replanned`, `dss_replanned`, `utm_confirmed` flow support).
   - `user_planned` is only changed by explicit user planning actions (`plan`, `create_uav`, `reset_route`).
   - `agent_replanned` is separate and linked to `user_planned` through association metadata (`associated_user_planned_route_id`, timestamps).
   - `dss_replanned` is used for DSS strategic-conflict mitigation detours and is also linked to `user_planned`.
   - Approval/verify flows do not auto-overwrite `user_planned`.
6. `mission_records` and approved path history  
   Mission-level approval and execution references.
7. `agent_actions`  
   Auditable action log and sync revision.

## 4) Core UAV Mission Workflow (UI or API driven)

The normal safe mission flow is:

1. Plan route  
   `POST /api/uav/sim/plan` or `POST /api/uav/live/plan`
2. Run UTM mission submission workflow  
   `POST /api/uav/sim/utm-submit-mission` or `POST /api/uav/live/utm-submit-mission`
3. Launch  
   `POST /api/uav/sim/launch` or `POST /api/uav/live/launch`
4. Execute ticks  
   `POST /api/uav/sim/step` or `POST /api/uav/live/step`
5. Apply control actions as needed  
   `hold`, `resume`, `rth`, `land` (also under `/api/uav/live/*`)

### What `utm-submit-mission` orchestrates

`/api/uav/sim/utm-submit-mission` (and `/api/uav/live/utm-submit-mission`) performs backend-side ordered workflow:

1. Route checks (`/api/utm/checks/route` logic).
2. Geofence submit (`/api/uav/sim/geofence-submit` or `/api/uav/live/geofence-submit`).
3. UTM verify from UAV route (`/api/utm/verify-from-uav` logic).
4. Approval request if verify is approved (`/api/uav/sim/request-approval` or `/api/uav/live/request-approval`).

This centralizes ordering and keeps session + DB updates on backend.

Mission planner UI rule:

1. If editor source is `user_planned`, `Submit to UTM (Auto)` first saves the user path, then runs UTM workflow.
2. If editor source is `agent_replanned`, `dss_replanned`, or `utm_confirmed`, `Submit to UTM (Auto)` submits current route for UTM processing without overwriting `user_planned`.

### Flight control gate

Before `launch`, `step`, `hold`, `resume`, `rth`, `land`, UAV API enforces gate checks:

1. Valid planned route exists.
2. Geofence/NFZ result exists and passed.
3. UTM approval exists and is valid.
4. Weather/regulation/license checks are not failed.
5. Battery and mission safety constraints are acceptable.

If any check fails, control command is blocked with a structured error.

Action-order logic (realistic operation constraints):

1. `launch` is idempotent:
   - If UAV is already launched (`armed=true`), backend returns warning and does not relaunch.
2. `step`, `hold`, `resume`, `rth`, `land` require launched state (`armed=true`):
   - If not launched, backend returns issue: `Please launch before <action>.`
3. Additional operation-state checks:
   - `step` requires `active=true` (otherwise resume first).
   - `hold` requires `active=true`.
   - `resume` is blocked if already active.
   - `rth` is blocked when already in `RTH`.
   - `land` is blocked when already in `LAND`.
4. UI shows these as warning messages (yellow/orange text), not only hard errors.

## 5) Copilot Workflow (`/api/uav/agent/chat`)

`POST /api/uav/agent/chat` runs `run_copilot_workflow(...)`.

Graph stages:

1. Build context from UAV + UTM + network.
2. Planner node chooses actions.
3. Execute node runs actions:
   - `replan_route`
   - `verify_flight_plan`
   - `network_optimize`
   - `hold`
4. Summary node generates concise assistant output.
5. Finalize returns:
   - messages
   - tool trace
   - updated UAV state
   - UTM verify result
   - network optimization result

If LLM planner is unavailable, API falls back to heuristic copilot path.

Important persistence behavior:

1. Copilot writes replan history only when a replan action succeeds (`agent_replanned` by default, `dss_replanned` for DSS-conflict mitigation context).
2. When copilot `auto_verify` returns UTM verification:
   - session approval/geofence are persisted (`uav_utm_sessions`),
   - simulator UTM state is synchronized from session,
   - if approved + signature verified, mission + approved route records are persisted (`mission_records`, approved history in UAV and UTM mirror DBs).
3. This makes `auto_verify` effective for launch/step flight gate checks.

## 6) NFZ Replan Loop (`/api/uav/sim/replan-via-utm-nfz`)

This endpoint performs route replan with optional automatic UTM re-verify loop:

1. Replan route around NFZ constraints.
2. Optionally verify with UTM.
3. If still conflict and retryable, regenerate route and retry.
4. Save route history and session state.
5. If approved, persist mission + approved route records.

Path category behavior in this flow:

1. Replanned routes are persisted under `agent_replanned`.
   - If `route_category=dss_replanned` (or DSS conflict mitigation context is used), they are persisted under `dss_replanned`.
2. `user_planned` remains the original user path and is not rewritten by replan verification/approval.

Home waypoint (`HM`) behavior:

1. `HM` (`WP0`) is treated as stable planned home.
2. Stepping the UAV does not move `HM`.
3. Confirming/saving user path does not auto-replace `HM` with current UAV live position.
4. `HM` changes only when user explicitly edits home/path (or explicitly changes home profile values).

Profiles:

1. `safe`
2. `balanced`
3. `aggressive`

These tune margin, overflight policy, and simplification behavior.

## 7) Interaction With Other Agents

### 7.1 UTM Agent

UAV agent interacts with UTM in three ways:

1. Direct service calls through shared `UTM_SERVICE` object (local process path).
2. Optional mirror sync from standalone UTM API (`UAV_REAL_UTM_API_BASE_URL`, default `http://127.0.0.1:8021`) before route/approval operations.
3. DB mirror writes (`AgentDB("utm")`) for approved route record consistency on UAV-side workflows.

Primary UTM checks used:

1. Route bounds
2. Weather
3. NFZ conflicts (waypoint + segment level)
4. Regulations (altitude/span/speed)
5. Time window
6. Operator license

### 7.2 Network Agent

UAV agent uses `NETWORK_MISSION_SERVICE` for:

1. Mission network state retrieval.
2. Optimization controls (`coverage`, `qos`, `power`).
3. Network overlays and telemetry context used by copilot.

### 7.3 Mission Supervisor Agent

Mission Supervisor is the top-level orchestrator for cross-domain requests.

Workflow:

1. Watchers refresh snapshots from UAV/UTM/Network.
2. Planner builds phase-specific plan (`preflight`, `launch`, `execution`, `mitigation`, `closeout`).
3. `domain_dispatch` delegates steps to:
   - UAV tools (`uav_plan_route`, `uav_request_utm_approval`, `uav_launch`, etc.)
   - UTM tools (`verify_flight_plan`, checks)
   - Network tools (`slice_apply_profile`, `kpm_monitor`, etc.)
4. Policy + lock manager + verify + rollback protect mission integrity.

In short:

1. UAV agent executes flight domain logic.
2. Mission supervisor decides when and in what order UAV/UTM/Network actions happen for cross-domain missions.

## 8) End-To-End Sequence (Typical)

### A) Direct UAV mission from UI

1. UI -> UAV API: plan route.
2. UAV API -> UTM checks + verify + approval.
3. UAV API -> persist session/profile/path/mission data.
4. UI -> UAV API: launch/step.
5. UAV API -> flight gate -> simulator updates.
6. UAV API -> log action + revision update.

### B) Cross-domain mission via Mission Supervisor

1. Client -> Mission Supervisor API: start mission request.
2. Supervisor watchers ingest UAV/UTM/Network state.
3. Supervisor planner builds ordered multi-domain plan.
4. Supervisor dispatches UAV + UTM + Network tool calls.
5. Supervisor verifies results, progresses, or rolls back.
6. Mission state/events are persisted in mission supervisor DB.

## 9) Key API Surfaces

Main UAV endpoints:

1. `/api/uav/sim/state` and `/api/uav/live/state`
2. `/api/uav/sim/plan` and `/api/uav/live/plan`
3. `/api/uav/sim/utm-submit-mission` and `/api/uav/live/utm-submit-mission`
4. `/api/uav/sim/launch`, `/step`, `/hold`, `/resume`, `/rth`, `/land`
5. `/api/uav/live/launch`, `/step`, `/hold`, `/resume`, `/rth`, `/land`
6. `/api/uav/sim/replan-via-utm-nfz` and `/api/uav/live/replan-via-utm-nfz`
7. `/api/uav/agent/chat`
8. `/api/uav/live/source`, `/api/uav/live/ingest`
9. `/api/uav/registry/assign`, `/registry/profile`, `/mission-defaults`
10. `/api/uav/demo/seed-user-profiles`
11. `/api/uav/live/control-adapter`
12. `/api/uav/control/_contract`, `/api/uav/control/{operation}`

UTM-facing routes mounted in UAV API:

1. `/api/utm/checks/route`
2. `/api/utm/verify-from-uav`
3. `/api/utm/weather`, `/api/utm/nfz`, `/api/utm/license`

Network-facing routes mounted in UAV API:

1. `/api/network/mission/state`
2. `/api/network/optimize`
3. `/api/network/mission/tick`

## 10) Control Uplink Adapter

Control commands (`launch`, `step`, `hold`, `resume`, `rth`, `land`) now execute through a pluggable uplink adapter:

1. `sim` (default): current internal simulator path.
2. `http`: external command bridge for real UAV stacks (MAVLink, DJI, etc. behind bridge).
3. `auto`: uses `http` for non-simulated data sources when configured, otherwise `sim`.

Environment controls:

1. `UAV_CONTROL_ADAPTER_MODE` = `sim|http|auto|mavlink|dji`
2. `UAV_CONTROL_HTTP_BASE_URL` (e.g. hardware bridge API)
3. `UAV_CONTROL_HTTP_PATH_TEMPLATE` (default `/api/uav/control/{op}`)
4. `UAV_CONTROL_HTTP_TIMEOUT_S`
5. `UAV_CONTROL_HTTP_AUTH_TOKEN`
6. `UAV_CONTROL_MIRROR_MODE` = `optimistic|telemetry_only|none`
7. `UAV_CONTROL_ADAPTER_FALLBACK_TO_SIM` = `1|0`

When `http` mode succeeds, telemetry in the uplink response is ingested into simulator state. If no telemetry is returned and mirror mode is `optimistic`, local state is updated via simulator semantics to preserve existing mission flow.

Bridge contract:

1. `GET /api/uav/control/_contract` returns request/response JSON schema and examples.
2. `POST /api/uav/control/{operation}` is a sample MAVLink bridge stub endpoint implementing:
   - request fields: `uav_id`, `operation`, `params`, `requested_at`, `command_id`, `idempotency_key`, `caller`
   - response fields: `status`, `adapter`, `command`, `telemetry`, `result`, `error`, `details`
3. Telemetry payload shape is adapter-compatible with `UAV_CONTROL_HTTP_BASE_URL` uplink integration.

## 11) Operational Notes

Default local ports from `run-dev.sh`:

1. UAV API: `8020`
2. UTM API: `8021`
3. Network API: `8022`
4. LangGraph dev backend (graphs): `2024`

Because UAV API imports both UTM and Network shared services, it can serve a unified mission control surface while still syncing with standalone UTM/Network APIs when configured.

## 12) Future Work

1. Pending deployment-specific wiring: configure real DJI/MAVLink bridge URL profile once production bridge endpoint details are provided.
