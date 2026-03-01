# UTM DSS Gap-Closure Roadmap (MVP -> Pre-Cert -> Cert)

## 0) What Was Downloaded Locally
InterUSS repositories cloned under `external/interuss/`:

1. `dss`
2. `monitoring`
3. `stacktrace`
4. `tsc`
5. `implicitdict`
6. `uas_standards`
7. `automated_testing_interfaces`
8. `astm-utm-protocol`
9. `yugabyte-charts`
10. `geospatial-utils`

Primary references for implementation behavior:

- `external/interuss/dss`
- `external/interuss/monitoring`
- `external/interuss/astm-utm-protocol`

## 1) Current System Baseline (Your Code)

### Current strengths
1. End-to-end route verification and approval flow is implemented and enforced.
2. Flight-control gate blocks unsafe actions with explicit issues.
3. UTM checks include weather, NFZ, route bounds, regulations, time window, and operator license.
4. Mission supervisor has policy gate, lock manager, evidence, and rollback semantics.

### Key module map (today)
1. UTM policy/approval engine: `backend/utm_agent/service.py`
2. UTM API surface: `backend/utm_agent/api.py`, `backend/uav_agent/api_routes_utm.py`
3. UAV mission+approval orchestration: `backend/uav_agent/api_routes_uav.py`
4. Flight gating and session sync: `backend/uav_agent/api_shared.py`
5. Cross-domain orchestration: `backend/mission_supervisor_agent/graph.py`, `planner.py`, `policy.py`, `domain_dispatch.py`, `watchers.py`

## 2) Mismatch vs DSS/USSP-Like Platform

### Workflow mismatch (DSS/interoperability)
1. No federated USS registry or multi-USSP discovery plane (DSS-style shared discovery/sync).
2. No operational intent publication lifecycle shared among multiple USS participants.
3. No DSS subscription mechanism for airspace-volume change notifications.
4. `reserve_corridor` is currently a stub, not strategic coordination logic.
5. Current flow is mostly single-operator/single-service with optional mirror sync, not distributed ecosystem coordination.

### Certification mismatch (FAA NTAP + EASA USSP direction)
1. No formal requirement traceability matrix (RTM) from rules/standards to test evidence.
2. No structured compliance package generation (safety case, continuity, cyber, incident response, software assurance).
3. No standards-conformance automation like uss_qualifier-equivalent in CI gating.
4. Approval signature semantics are simulator-level (`signature_verified` flag), not strong trust/identity controls.
5. No explicit audited role model for USSP/CISP responsibilities and regulator-facing oversight workflows.

## 3) Target Agent-Based Architecture (DSS-Like)

### Services to add
1. `dss_registry_service` (federated entity registry abstraction)
2. `operational_intent_service` (intent lifecycle + state transitions)
3. `subscription_service` (4D volume subscriptions + notifications)
4. `interuss_sync_adapter` (ASTM/F3548 API adapters)
5. `conformance_service` (automatic scenario execution and evidence generation)
6. `compliance_case_service` (RTM, requirement status, artifacts)

### Agent responsibilities
1. `UTM Policy Agent`
- Local safety rule evaluation, time/weather/NFZ/license checks.
2. `DSS Coordination Agent`
- Publish/update/delete operational intents, query intersecting intents, manage subscriptions.
3. `Conformance Agent`
- Run qualification scenarios (ASTM + local jurisdiction overlays), produce pass/fail and evidence.
4. `Certification Agent`
- Build readiness pack: controls mapping, logs, evidence index, unresolved findings.
5. `Supervisor Agent`
- Orchestrate cross-agent workflows and enforce release gates.

## 4) Concrete Roadmap

## Phase A: MVP (DSS-capable core, 6-10 weeks)

### Objectives
1. Move from single-node UTM checks to DSS-like multi-party strategic coordination primitives.
2. Keep existing flight gate behavior while adding interoperability APIs.

### Deliverables
1. Add operational intent model and store.
- New module: `backend/utm_agent/operational_intents.py`
- Fields: `intent_id`, `manager_uss_id`, `state`, `priority`, `volume4d`, `ovn`, `version`, `time_start`, `time_end`, `constraints`, `updated_at`.

2. Add subscription model and query engine.
- New module: `backend/utm_agent/subscriptions.py`
- Endpoints:
  - `POST /api/utm/dss/subscriptions`
  - `GET /api/utm/dss/subscriptions`
  - `DELETE /api/utm/dss/subscriptions/{id}`

3. Add operational intent endpoints.
- In `backend/utm_agent/api.py`:
  - `POST /api/utm/dss/operational-intents`
  - `GET /api/utm/dss/operational-intents/query`
  - `PUT /api/utm/dss/operational-intents/{id}`
  - `DELETE /api/utm/dss/operational-intents/{id}`

4. Add strategic conflict check on intent publish/update.
- Extend `backend/utm_agent/service.py` with 4D overlap logic and conflict status classes.

5. Add mission supervisor awareness of DSS intents.
- Update `backend/mission_supervisor_agent/watchers.py` to ingest DSS conflict indicators.
- Update `backend/mission_supervisor_agent/planner.py` to include mitigation actions when strategic conflicts exist.

6. Add adapter interface for external DSS.
- New module: `backend/utm_agent/dss_adapter.py`
- Start with local implementation, then external HTTP adapter.

### Exit criteria
1. Intent publication/query/subscription flows work end-to-end.
2. Strategic conflicts can be detected before launch.
3. Mission supervisor uses DSS conflict state in policy/planning.

## Phase B: Pre-Cert (compliance engineering, 8-12 weeks)

### Objectives
1. Make behavior testable and traceable against standards/jurisdiction requirements.
2. Achieve repeatable conformance evidence generation.

### Deliverables
1. Requirements Traceability Matrix (RTM).
- New artifact: `docs/compliance/rtm.yaml`
- Map each requirement to code paths, tests, evidence IDs.

2. Automated conformance test harness.
- New package: `backend/test/utm_conformance/`
- Scenario categories:
  - Operational intent lifecycle
  - Multi-USSP conflict resolution
  - Time-window and dynamic airspace updates
  - Failure handling and stale-data handling

3. Monitoring/evidence pipeline.
- Structured immutable event IDs and correlation IDs for each UTM decision.
- Artifacts persisted for audit export.

4. Security and trust hardening.
- Service-to-service authn/authz for UTM/DSS APIs.
- Signature validation for exchanged intent objects.
- Key rotation policy and trust-store config.

5. Operational resilience controls.
- Explicit SLOs (availability, decision latency, recovery time).
- Degraded-mode behavior when DSS peer unavailable.

### Exit criteria
1. RTM coverage >= 90% for implemented services.
2. Automated conformance suite runs in CI and blocks regressions.
3. Evidence pack can be generated on-demand for a release.

## Phase C: Cert Readiness (authority-facing, 12+ weeks)

### Objectives
1. Transition from engineering compliance to regulator-auditable organizational compliance.
2. Produce submission-ready package(s) for target jurisdiction.

### Deliverables
1. Certification pack generator.
- Safety case, cyber controls, incident process, continuity plans, software assurance records.
- Machine-generated index linking claims to evidence.

2. Jurisdiction profiles.
- `profiles/us_faa_ntap.yaml`
- `profiles/eu_ussp_2021_664.yaml`
- `profiles/icao_framework_alignment.yaml`

3. Governance and change-control workflow.
- Formal release gate requiring conformance results + compliance approvals.
- Exception handling and deviation logs.

4. External interoperability campaigns.
- Scheduled multi-party test runs with partner USS implementations.
- Evidence export for independent review.

### Exit criteria
1. No unresolved critical findings in pre-audit checklist.
2. Repeatable audit artifact generation completed for at least 2 consecutive releases.
3. Successful external interoperability campaign with signed report.

## 5) How to Build a Similar Platform with Agent Approach

1. Define authority profile first (FAA NTAP and/or EU USSP) before coding.
2. Build standards DTOs/contracts as first-class modules (`uas_standards`-style typing).
3. Implement DSS primitives (intent + subscription + discovery) before advanced AI orchestration.
4. Add policy engine and flight gate as deterministic layer (AI cannot override safety rules).
5. Layer agents on top:
- Planner agent proposes actions.
- Policy agent validates.
- DSS coordination agent executes interoperability operations.
- Certification agent records traceability and evidence.
6. Enforce all mission decisions through a single auditable command bus.
7. Add conformance suite early and make it CI-blocking.
8. Introduce external interoperability adapters only after local deterministic tests pass.
9. Add operational SLOs + incident playbooks before pilot operations.
10. Run periodic red-team and failure-injection tests for resilience and cyber posture.

### 5.1 Current status check (2026-02-27)

1. `implemented` Authority profiles exist and are loaded by API/cert pack (`profiles/*.yaml`, `backend/utm_agent/cert_pack.py`, `backend/utm_agent/api.py`).
2. `implemented` Standards contracts module added (`backend/utm_agent/contracts.py`) and wired into DSS intent/subscription ingestion.
3. `implemented` DSS primitives are in place for intents/subscriptions/participants/notifications (`backend/utm_agent/operational_intents.py`, `subscriptions.py`, `api.py`).
4. `implemented` Deterministic policy and flight gate are active (`backend/utm_agent/service.py`, `backend/uav_agent/api_shared.py`).
5. `implemented` Agent layering exists with planner/policy/DSS/certification flows and signed DSS intent exchange checks.
6. `implemented` Single auditable command bus is active for supervisor execution (`backend/mission_supervisor_agent/command_bus.py`, `graph.py`).
7. `implemented` Conformance suite is CI-blocking (`backend/test/run_utm_conformance_ci.sh`, `.github/workflows/utm-conformance.yml`).
8. `implemented` External DSS adapter usage is conformance-gated with enforced failover policy in live dispatcher paths (`backend/utm_agent/dss_gateway.py`, `backend/utm_agent/tools.py`, `backend/uav_agent/api_shared.py`).
9. `implemented` Operational SLO + incident playbook readiness checks are available (`backend/utm_agent/operations_readiness.py`, `docs/operations/*`, `api.py`).
10. `implemented` Periodic resilience/red-team campaign framework is available (`backend/utm_agent/resilience_campaigns.py`, `api.py`).

## 6) Immediate Power-Up Backlog for Your Current UTM Agent

Status (2026-02-27):

1. `implemented` DSS-style operational intent endpoints and persistence are active.
2. `implemented` Conflict-resolution policy modes (`reject`, `negotiate`, `conditional_approve`) are active.
3. `implemented` `reserve_corridor` now uses intent reservation with lease/version semantics.
4. `partial` Subscription notifications are implemented via internal queue/event bus; USS-to-USS callback delivery adapter remains pending.
5. `partial` Signed DSS intent objects and signature verification are implemented; signed UTM decision objects/verifier middleware for all decision paths remains pending.
6. `implemented` Conformance scenario runner is integrated in `backend/test` and CI.
7. `implemented` Compliance artifact export endpoint (`/api/utm/compliance/export`) is active.

## 7) Module-by-Module Change Plan

Status (2026-02-27):

1. `implemented` `backend/utm_agent/service.py`
- 4D intent graph/conflict utilities and lease/version reservation helpers added.

2. `implemented` `backend/utm_agent/api.py`
- DSS endpoints, conformance status endpoints, and compliance export endpoints are active.

3. `implemented` `backend/utm_agent/tools.py`
- Intent publish/update/query/subscribe, conformance run, and conformance status tools are active.

4. `implemented` `backend/uav_agent/api_routes_uav.py`
- Approval path now publishes/refreshes DSS intent and blocks on DSS publish errors/conflicts.

5. `implemented` `backend/uav_agent/api_shared.py`
- Flight gate now checks DSS conflicts plus stale-subscription/degraded/error states.

6. `implemented` `backend/mission_supervisor_agent/watchers.py`
- DSS ecosystem status includes conflict classes, subscription staleness, and notification lag.

7. `implemented` `backend/mission_supervisor_agent/planner.py`
- Mitigation/replan branches now include DSS conflict-class and lag-aware actions.

8. `implemented` `backend/mission_supervisor_agent/policy.py`
- Compliance guardrails keyed by jurisdiction profile are enforced in policy validation.

9. `implemented` `backend/mission_supervisor_agent/domain_dispatch.py`
- DSS operations and DSS conformance-status observe operations are dispatched.

## 8) Definition of Done per Phase

### MVP DoD
1. Interoperable intent lifecycle exists with deterministic conflict outcomes.
2. Existing launch gate includes DSS conflict checks.
3. Evidence logs include intent IDs and versions for every decision.

### Pre-Cert DoD
1. RTM complete for implemented requirements.
2. CI executes conformance scenarios and stores artifacts.
3. Security controls and key management validated in staging.

### Cert DoD
1. Audit package auto-generated with traceable evidence.
2. External interoperability results accepted by review board.
3. Release process enforces compliance sign-off.
