"""UTM API (supports simulator defaults and live-ingested data)."""

from __future__ import annotations

import os
import json
import hashlib
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError("utm_agent.api requires fastapi and pydantic") from e

from uav_agent.simulator import SIM
from utm_agent.service import UTM_SERVICE, reserve_corridor_with_lease
from utm_agent.operational_intents import (
    delete_intent as dss_delete_intent,
    query_intents as dss_query_intents,
    upsert_intent as dss_upsert_intent,
)
from utm_agent.subscriptions import (
    delete_subscription as dss_delete_subscription,
    impacted_subscriptions as dss_impacted_subscriptions,
    query_subscriptions as dss_query_subscriptions,
    upsert_subscription as dss_upsert_subscription,
)
from utm_agent.dss_gateway import (
    gateway_delete_operational_intent,
    gateway_delete_subscription,
    gateway_query_operational_intents,
    gateway_query_subscriptions,
    gateway_upsert_operational_intent,
    gateway_upsert_subscription,
)
from utm_agent.layered_status import build_layered_status
from utm_agent.cert_pack import (
    build_certification_pack,
    list_available_profiles,
    load_jurisdiction_profile,
    parse_rtm_requirements,
)
from utm_agent.release_governance import (
    create_deviation_record,
    evaluate_release_gate,
    resolve_deviation_record,
)
from utm_agent.interoperability_campaigns import (
    append_campaign_run,
    build_campaign_report,
    create_campaign,
    sign_campaign_report,
)
from utm_agent.operations_readiness import (
    build_incident_playbook_index,
    evaluate_operations_readiness,
)
from utm_agent.resilience_campaigns import (
    append_resilience_run,
    build_default_failure_injection_results,
    build_resilience_summary,
    create_resilience_campaign,
)
from utm_agent.security_controls import (
    authorize_service_request,
    ensure_security_state,
    register_peer_key,
    rotate_signing_key,
)
from agent_db import AgentDB


class WeatherPayload(BaseModel):
    airspace_segment: str = "sector-A3"
    wind_mps: float = 8.0
    visibility_km: float = 10.0
    precip_mmph: float = 0.0
    storm_alert: bool = False


class LicensePayload(BaseModel):
    operator_license_id: str
    license_class: str = "VLOS"
    uav_size_class: str = "middle"
    expires_at: str = "2099-01-01T00:00:00Z"
    active: bool = True


class NoFlyZonePayload(BaseModel):
    zone_id: Optional[str] = None
    cx: float
    cy: float
    radius_m: float = 30.0
    z_min: float = 0.0
    z_max: float = 120.0
    reason: str = "operator_defined"


class WaypointPayload(BaseModel):
    x: float
    y: float
    z: float
    action: Optional[str] = None


class CorridorPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    manager_uss_id: str = "uss-local"
    conflict_policy: str = "reject"
    lease_ttl_s: int = 300
    route_id: Optional[str] = None


class RouteCheckPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    requested_speed_mps: float = 12.0
    operator_license_id: Optional[str] = None
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointPayload]] = None


class TimeWindowCheckPayload(BaseModel):
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None
    operator_license_id: Optional[str] = None


class LicenseCheckPayload(BaseModel):
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"


class VerifyFromUavPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"
    requested_speed_mps: float = 12.0
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointPayload]] = None


class UtmLiveIngestPayload(BaseModel):
    source: str = "live_utm_feed"
    source_ref: Optional[str] = None
    observed_at: Optional[str] = None
    airspace_segment: str = "sector-A3"
    weather: Optional[Dict[str, Any]] = None
    no_fly_zones: Optional[List[Dict[str, Any]]] = None
    regulations: Optional[Dict[str, Any]] = None
    licenses: Optional[Dict[str, Dict[str, Any]]] = None


class Volume4DPayload(BaseModel):
    x_min: Optional[float] = None
    x_max: Optional[float] = None
    y_min: Optional[float] = None
    y_max: Optional[float] = None
    z_min: Optional[float] = None
    z_max: Optional[float] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    x: Optional[List[float]] = None
    y: Optional[List[float]] = None
    z: Optional[List[float]] = None
    bounds: Optional[Dict[str, float]] = None

    def as_volume4d(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if isinstance(self.x, list):
            out["x"] = [float(v) for v in self.x[:2]]
        if isinstance(self.y, list):
            out["y"] = [float(v) for v in self.y[:2]]
        if isinstance(self.z, list):
            out["z"] = [float(v) for v in self.z[:2]]
        if self.x_min is not None:
            out["x_min"] = float(self.x_min)
        if self.x_max is not None:
            out["x_max"] = float(self.x_max)
        if self.y_min is not None:
            out["y_min"] = float(self.y_min)
        if self.y_max is not None:
            out["y_max"] = float(self.y_max)
        if self.z_min is not None:
            out["z_min"] = float(self.z_min)
        if self.z_max is not None:
            out["z_max"] = float(self.z_max)
        if isinstance(self.bounds, dict):
            out["bounds"] = dict(self.bounds)
        if self.time_start:
            out["time_start"] = str(self.time_start)
        if self.time_end:
            out["time_end"] = str(self.time_end)
        return out


class OperationalIntentPayload(BaseModel):
    intent_id: Optional[str] = None
    manager_uss_id: str = "uss-local"
    state: str = "accepted"
    priority: str = "normal"
    conflict_policy: str = "reject"
    ovn: Optional[str] = None
    uss_base_url: Optional[str] = None
    volume4d: Volume4DPayload
    constraints: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    signature: Optional[Dict[str, Any]] = None


class OperationalIntentQueryPayload(BaseModel):
    manager_uss_id: Optional[str] = None
    states: Optional[List[str]] = None
    volume4d: Optional[Volume4DPayload] = None


class SubscriptionPayload(BaseModel):
    subscription_id: Optional[str] = None
    manager_uss_id: str = "uss-local"
    uss_base_url: str = ""
    callback_url: str = ""
    volume4d: Volume4DPayload
    notify_for: Optional[List[str]] = None
    expires_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SubscriptionQueryPayload(BaseModel):
    manager_uss_id: Optional[str] = None
    volume4d: Optional[Volume4DPayload] = None


class ParticipantPayload(BaseModel):
    participant_id: str
    uss_base_url: str = "http://127.0.0.1:9000"
    roles: List[str] = ["uss"]
    status: str = "active"
    metadata: Optional[Dict[str, Any]] = None


class ConformanceRunPayload(BaseModel):
    reset_before_run: bool = True


class DssNotificationDispatchPayload(BaseModel):
    run_limit: int = 1


class DssNotificationDispatcherEnabledPayload(BaseModel):
    enabled: bool = True


class CertificationApprovalPayload(BaseModel):
    role: str
    approver: str = "unknown"
    status: str = "approved"
    note: Optional[str] = None


class CertificationPackGeneratePayload(BaseModel):
    jurisdiction_profile: str = "us_faa_ntap"
    release_id: str = "release-local"
    candidate_version: str = "0.0.0-dev"
    approvals: Optional[List[CertificationApprovalPayload]] = None
    notes: Optional[str] = None
    include_action_payloads: bool = False
    include_rtm_text: bool = False
    evidence_limit_actions: int = 200


class ReleaseGateEvaluatePayload(BaseModel):
    release_id: str = "release-local"
    pack_id: Optional[str] = None
    required_approvals: Optional[List[str]] = None
    require_signed_campaign_report: bool = False
    enforce_critical_findings: bool = False
    campaign_id: Optional[str] = None
    notes: Optional[str] = None


class DeviationPayload(BaseModel):
    release_id: str = "release-local"
    category: str = "compliance"
    severity: str = "major"
    description: str
    rationale: Optional[str] = None
    mitigation_plan: Optional[str] = None
    owner: str = "unknown"
    status: str = "open"
    metadata: Optional[Dict[str, Any]] = None


class DeviationResolvePayload(BaseModel):
    status: str = "resolved"
    resolver: str = "unknown"
    resolution_note: Optional[str] = None


class InteropCampaignStartPayload(BaseModel):
    campaign_id: Optional[str] = None
    name: str = "interoperability_campaign"
    jurisdiction_profile: str = "us_faa_ntap"
    release_id: str = "release-local"
    partners: List[str] = ["uss-local"]
    scenarios: List[str] = ["operational_intent_lifecycle", "conflict_resolution", "notification_delivery"]
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    created_by: str = "utm"
    notes: Optional[str] = None


class InteropCampaignRunPayload(BaseModel):
    partner_id: str
    scenario_id: str
    status: str = "passed"
    summary: Optional[str] = None
    evidence_ids: Optional[List[str]] = None
    metrics: Optional[Dict[str, Any]] = None


class InteropCampaignSignPayload(BaseModel):
    signed_by: str
    signature_ref: str
    decision: str = "accepted"
    note: Optional[str] = None


class OperationsReadinessPayload(BaseModel):
    jurisdiction_profile: str = "us_faa_ntap"
    observed_metrics: Optional[Dict[str, Any]] = None


class ResilienceCampaignStartPayload(BaseModel):
    campaign_id: Optional[str] = None
    name: str = "resilience_campaign"
    release_id: str = "release-local"
    cadence_days: int = 30
    created_by: str = "utm"
    scenarios: Optional[List[str]] = None
    notes: Optional[str] = None


class ResilienceCampaignRunPayload(BaseModel):
    executed_by: str = "qa"
    fault_profile: str = "baseline"
    summary: Optional[str] = None
    scenario_results: Optional[List[Dict[str, Any]]] = None


class SecurityPeerKeyPayload(BaseModel):
    issuer: str
    key_id: str
    secret: str
    status: str = "active"


class SecurityRotateKeyPayload(BaseModel):
    issuer: str = "utm-local"


class SecurityTokenPayload(BaseModel):
    token: str
    roles: List[str] = ["read"]


class SecurityRotationPolicyPayload(BaseModel):
    max_age_days: int = 30
    overlap_days: int = 7
    auto_rotate: bool = False


def _sim_waypoints(uav_id: str) -> tuple[str, list[dict]]:
    sim = SIM.status(uav_id)
    route_id = str(sim.get("route_id", "route-1"))
    waypoints = list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else []
    return route_id, waypoints


def _geofence_check_from_waypoints(*, uav_id: str, route_id: str, airspace_segment: str, waypoints: list[dict]) -> Dict[str, Any]:
    route_bounds = UTM_SERVICE.check_route_bounds(airspace_segment, waypoints)
    nfz = UTM_SERVICE.check_no_fly_zones(waypoints)
    out_of_bounds = route_bounds.get("out_of_bounds") if isinstance(route_bounds.get("out_of_bounds"), list) else []
    bounds_ok = bool(
        route_bounds.get("ok") is True
        or route_bounds.get("geofence_ok") is True
        or route_bounds.get("bounds_ok") is True
    )
    return {
        "uav_id": uav_id,
        "route_id": route_id,
        "airspace_segment": airspace_segment,
        # Geofence is route-bounds only. NFZ is reported separately in `no_fly_zone`.
        "ok": bounds_ok,
        "geofence_ok": bounds_ok,
        "bounds_ok": bounds_ok,
        "out_of_bounds": out_of_bounds,
        "bounds": route_bounds.get("bounds"),
        "matched_airspace": route_bounds.get("matched_airspace"),
        "source": route_bounds.get("source"),
        "no_fly_zone": nfz,
    }


app = FastAPI(title="UTM Simulator API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UTM_DB = AgentDB("utm")
UAV_DB = AgentDB("uav")
UTM_DATA_MODE = os.getenv("UTM_DATA_MODE", "auto").strip().lower() or "auto"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RTM_PATH = PROJECT_ROOT / "docs" / "compliance" / "rtm.yaml"
PROFILES_DIR = PROJECT_ROOT / "profiles"
PLAYBOOKS_DIR = PROJECT_ROOT / "docs" / "operations"
CERTIFICATION_DOCS_DIR = PROJECT_ROOT / "docs" / "compliance" / "certification"
CERTIFICATION_EXPORTS_DIR = CERTIFICATION_DOCS_DIR / "exports"


def _enforce_service_auth() -> bool:
    raw = str(os.getenv("UTM_ENFORCE_SERVICE_AUTH", "true") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@app.middleware("http")
async def _service_auth_middleware(request: Request, call_next):
    path = str(request.url.path or "")
    method = str(request.method or "GET").upper()
    if not path.startswith("/api/utm"):
        return await call_next(request)
    # Always allow CORS preflight requests.
    if method == "OPTIONS":
        return await call_next(request)
    # Keep liveness-style endpoints open for local orchestration probes.
    if path in {"/api/utm/state", "/api/utm/live/source", "/api/utm/layers/status"} and method == "GET":
        return await call_next(request)
    decision = authorize_service_request(
        path=path,
        method=method,
        authorization_header=str(request.headers.get("authorization", "")),
        state=_get_security_controls(),
        enforce=_enforce_service_auth(),
    )
    if decision.get("ok") is not True:
        status_code = 401 if str(decision.get("error", "")).startswith("missing_") else 403
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "error",
                "error": decision.get("error"),
                "required_role": decision.get("required_role"),
            },
        )
    return await call_next(request)


@app.on_event("startup")
def _startup_dss_notification_dispatcher() -> None:
    if not _dss_dispatcher_enabled():
        return
    _start_dss_notification_dispatcher()


@app.on_event("shutdown")
def _shutdown_dss_notification_dispatcher() -> None:
    _stop_dss_notification_dispatcher()


def _restore_utm_state() -> None:
    state = UTM_DB.get_state("store")
    if isinstance(state, dict):
        UTM_SERVICE.load_state(state)


def _persist_utm_state() -> None:
    UTM_DB.set_state("store", UTM_SERVICE.export_state())


def _log_utm_action(action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    _persist_utm_state()
    return UTM_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)


_restore_utm_state()


def _layered_fleet_snapshot() -> Dict[str, Dict[str, Any]]:
    # Use UAV DB as authoritative fleet source so UTM layered cards stay aligned with UAV page
    # across separate service processes.
    raw = UAV_DB.get_state("fleet")
    if isinstance(raw, dict):
        out = {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}
        if out:
            return out
    raw_sim = SIM.fleet_snapshot()
    return {str(k): dict(v) for k, v in raw_sim.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(raw_sim, dict) else {}


def _utm_data_mode() -> str:
    mode = UTM_DATA_MODE
    return mode if mode in {"sim", "real", "auto"} else "auto"


def _utm_live_meta() -> Dict[str, Any] | None:
    state = UTM_DB.get_state("live_meta")
    return dict(state) if isinstance(state, dict) else None


def _utm_source_info() -> Dict[str, Any]:
    meta = _utm_live_meta()
    active = "live" if isinstance(meta, dict) else "simulated"
    return {"mode": _utm_data_mode(), "active": active, "meta": meta}


def _get_dss_operational_intents() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("dss_operational_intents")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _set_dss_operational_intents(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("dss_operational_intents", values)


def _get_dss_subscriptions() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("dss_subscriptions")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _set_dss_subscriptions(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("dss_subscriptions", values)


def _get_dss_participants() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("dss_participants")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _set_dss_participants(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("dss_participants", values)


def _get_dss_notifications() -> List[Dict[str, Any]]:
    raw = UTM_DB.get_state("dss_notifications")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _set_dss_notifications(values: List[Dict[str, Any]]) -> None:
    UTM_DB.set_state("dss_notifications", values)


def _dss_runtime_snapshot() -> Dict[str, Any]:
    local_intents = _get_dss_operational_intents()
    local_subscriptions = _get_dss_subscriptions()
    intents: List[Dict[str, Any]] = list(local_intents.values())
    subscriptions: List[Dict[str, Any]] = list(local_subscriptions.values())

    intents_out = gateway_query_operational_intents(UTM_DB, {"manager_uss_id": None, "states": None, "volume4d": None})
    subs_out = gateway_query_subscriptions(UTM_DB, {"manager_uss_id": None, "volume4d": None})

    if str(intents_out.get("status", "")).strip().lower() == "success":
        out_result = intents_out.get("result") if isinstance(intents_out.get("result"), dict) else {}
        items = out_result.get("items") if isinstance(out_result.get("items"), list) else []
        intents = [dict(v) for v in items if isinstance(v, dict)]
    if str(subs_out.get("status", "")).strip().lower() == "success":
        out_result = subs_out.get("result") if isinstance(subs_out.get("result"), dict) else {}
        items = out_result.get("items") if isinstance(out_result.get("items"), list) else []
        subscriptions = [dict(v) for v in items if isinstance(v, dict)]

    participants = list(_get_dss_participants().values())
    notifications = _get_dss_notifications()
    return {
        "operationalIntents": intents,
        "subscriptions": subscriptions,
        "participants": participants,
        "notifications": notifications,
        "intents_adapter_mode": intents_out.get("adapter_mode"),
        "intents_degraded": intents_out.get("degraded"),
        "intents_failover_reason": intents_out.get("failover_reason"),
        "subscriptions_adapter_mode": subs_out.get("adapter_mode"),
        "subscriptions_degraded": subs_out.get("degraded"),
        "subscriptions_failover_reason": subs_out.get("failover_reason"),
    }


def _dss_dispatcher_config_enabled() -> bool:
    raw = str(os.getenv("UTM_DSS_NOTIFICATION_DISPATCHER_ENABLED", "false") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


_DSS_DISPATCH_RUNTIME_ENABLED: Optional[bool] = None


def _dss_dispatcher_enabled() -> bool:
    if isinstance(_DSS_DISPATCH_RUNTIME_ENABLED, bool):
        return _DSS_DISPATCH_RUNTIME_ENABLED
    return _dss_dispatcher_config_enabled()


def _dss_dispatch_interval_s() -> float:
    try:
        return max(0.2, float(os.getenv("UTM_DSS_NOTIFICATION_DISPATCH_INTERVAL_S", "1.0") or "1.0"))
    except Exception:
        return 1.0


def _dss_dispatch_timeout_s() -> float:
    try:
        return max(0.2, float(os.getenv("UTM_DSS_NOTIFICATION_DISPATCH_TIMEOUT_S", "3.0") or "3.0"))
    except Exception:
        return 3.0


def _dss_dispatch_batch_size() -> int:
    try:
        return max(1, min(200, int(os.getenv("UTM_DSS_NOTIFICATION_DISPATCH_BATCH_SIZE", "20") or "20")))
    except Exception:
        return 20


def _dss_dispatch_max_attempts() -> int:
    try:
        return max(1, min(100, int(os.getenv("UTM_DSS_NOTIFICATION_DISPATCH_MAX_ATTEMPTS", "8") or "8")))
    except Exception:
        return 8


_DSS_DISPATCH_STOP = threading.Event()
_DSS_DISPATCH_THREAD: threading.Thread | None = None
_DSS_DISPATCH_LOCK = threading.Lock()
_DSS_DISPATCH_LAST_CYCLE: Dict[str, Any] = {
    "updated_at": None,
    "attempted": 0,
    "delivered": 0,
    "failed": 0,
}


def _start_dss_notification_dispatcher() -> bool:
    global _DSS_DISPATCH_THREAD
    if isinstance(_DSS_DISPATCH_THREAD, threading.Thread) and _DSS_DISPATCH_THREAD.is_alive():
        return False
    _DSS_DISPATCH_STOP.clear()
    _DSS_DISPATCH_THREAD = threading.Thread(
        target=_run_dss_dispatch_loop,
        name="utm-dss-dispatcher",
        daemon=True,
    )
    _DSS_DISPATCH_THREAD.start()
    return True


def _stop_dss_notification_dispatcher() -> bool:
    global _DSS_DISPATCH_THREAD
    prev_alive = bool(isinstance(_DSS_DISPATCH_THREAD, threading.Thread) and _DSS_DISPATCH_THREAD.is_alive())
    _DSS_DISPATCH_STOP.set()
    if isinstance(_DSS_DISPATCH_THREAD, threading.Thread):
        try:
            _DSS_DISPATCH_THREAD.join(timeout=max(0.2, _dss_dispatch_interval_s() + 0.5))
        except Exception:
            pass
    if not (isinstance(_DSS_DISPATCH_THREAD, threading.Thread) and _DSS_DISPATCH_THREAD.is_alive()):
        _DSS_DISPATCH_THREAD = None
    return prev_alive


def _is_http_callback(url: str) -> bool:
    v = str(url or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _dss_notification_counts() -> Dict[str, int]:
    notifications = _get_dss_notifications()
    return {
        "total": len(notifications),
        "pending": len([n for n in notifications if str(n.get("status")) == "pending"]),
        "delivered": len([n for n in notifications if str(n.get("status")) == "delivered"]),
        "failed": len([n for n in notifications if str(n.get("status")) == "failed"]),
        "acked": len([n for n in notifications if str(n.get("status")) == "acked"]),
    }


def _dispatch_pending_dss_notifications_once() -> Dict[str, int]:
    global _DSS_DISPATCH_LAST_CYCLE
    with _DSS_DISPATCH_LOCK:
        notifications = _get_dss_notifications()
        if not notifications:
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            _DSS_DISPATCH_LAST_CYCLE = {
                "updated_at": now_iso,
                "attempted": 0,
                "delivered": 0,
                "failed": 0,
            }
            return {"attempted": 0, "delivered": 0, "failed": 0}

        attempted = 0
        delivered = 0
        failed = 0
        changed = False
        timeout_s = _dss_dispatch_timeout_s()
        batch_size = _dss_dispatch_batch_size()
        max_attempts = _dss_dispatch_max_attempts()

        for row in notifications:
            if attempted >= batch_size:
                break
            if not isinstance(row, dict):
                continue
            if str(row.get("status", "")).strip().lower() != "pending":
                continue
            callback_url = str(row.get("callback_url") or "").strip()
            if not _is_http_callback(callback_url):
                continue

            attempted += 1
            now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            prev_attempts = int(row.get("dispatch_attempts", 0) or 0)
            next_attempts = prev_attempts + 1
            row["dispatch_attempts"] = next_attempts
            row["last_attempt_at"] = now_iso
            changed = True

            payload = {
                "notification_id": str(row.get("notification_id") or ""),
                "event_type": str(row.get("event_type") or ""),
                "source_intent_id": str(row.get("source_intent_id") or ""),
                "subscription_id": str(row.get("subscription_id") or ""),
                "manager_uss_id": str(row.get("manager_uss_id") or ""),
                "callback_url": callback_url,
                "uss_base_url": str(row.get("uss_base_url") or ""),
                "created_at": str(row.get("created_at") or ""),
            }
            body = json.dumps(payload).encode("utf-8")
            req = UrlRequest(
                url=callback_url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            try:
                with urlopen(req, timeout=timeout_s) as resp:  # nosec B310 - dispatcher targets configured callback URLs
                    code = int(getattr(resp, "status", 200) or 200)
                if code < 400:
                    row["status"] = "delivered"
                    row["delivered_at"] = now_iso
                    row["last_error"] = ""
                    delivered += 1
                else:
                    row["last_error"] = f"http_status:{code}"
                    if next_attempts >= max_attempts:
                        row["status"] = "failed"
                        failed += 1
            except HTTPError as exc:
                row["last_error"] = f"http_error:{int(exc.code)}"
                if next_attempts >= max_attempts:
                    row["status"] = "failed"
                    failed += 1
            except URLError as exc:
                row["last_error"] = f"connection_error:{exc}"
                if next_attempts >= max_attempts:
                    row["status"] = "failed"
                    failed += 1
            except Exception as exc:
                row["last_error"] = f"unexpected_error:{exc}"
                if next_attempts >= max_attempts:
                    row["status"] = "failed"
                    failed += 1

        if changed:
            _set_dss_notifications(notifications[-5000:])
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _DSS_DISPATCH_LAST_CYCLE = {
            "updated_at": now_iso,
            "attempted": attempted,
            "delivered": delivered,
            "failed": failed,
        }
        return {"attempted": attempted, "delivered": delivered, "failed": failed}


def _run_dss_dispatch_loop() -> None:
    interval_s = _dss_dispatch_interval_s()
    while not _DSS_DISPATCH_STOP.is_set():
        try:
            _dispatch_pending_dss_notifications_once()
        except Exception:
            pass
        _DSS_DISPATCH_STOP.wait(interval_s)


def _get_certification_packs() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("certification_packs")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _set_certification_packs(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("certification_packs", values)


def _get_certification_doc_exports() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("certification_doc_exports")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _set_certification_doc_exports(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("certification_doc_exports", values)


def _get_release_gate_evaluations() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("release_gate_evaluations")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _set_release_gate_evaluations(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("release_gate_evaluations", values)


def _get_release_deviations() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("release_deviations")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _set_release_deviations(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("release_deviations", values)


def _get_interop_campaigns() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("interop_campaigns")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _set_interop_campaigns(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("interop_campaigns", values)


def _get_security_controls() -> Dict[str, Any]:
    raw = UTM_DB.get_state("security_controls")
    state = ensure_security_state(dict(raw) if isinstance(raw, dict) else None)
    if not isinstance(raw, dict):
        UTM_DB.set_state("security_controls", state)
    return state


def _set_security_controls(values: Dict[str, Any]) -> None:
    UTM_DB.set_state("security_controls", ensure_security_state(values))


def _get_resilience_campaigns() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB.get_state("resilience_campaigns")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _set_resilience_campaigns(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB.set_state("resilience_campaigns", values)


def _queue_dss_notifications(
    *,
    notifications: List[Dict[str, Any]],
    event_type: str,
    source_intent_id: str | None = None,
) -> List[Dict[str, Any]]:
    if not notifications:
        return []
    queue = _get_dss_notifications()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    queued: List[Dict[str, Any]] = []
    for rec in notifications:
        if not isinstance(rec, dict):
            continue
        row = {
            "notification_id": f"notif-{uuid4().hex[:12]}",
            "event_type": str(event_type or "update"),
            "source_intent_id": str(source_intent_id or ""),
            "subscription_id": str(rec.get("subscription_id") or ""),
            "manager_uss_id": str(rec.get("manager_uss_id") or ""),
            "callback_url": str(rec.get("callback_url") or ""),
            "uss_base_url": str(rec.get("uss_base_url") or ""),
            "notify_for": list(rec.get("notify_for") or []),
            "status": "pending",
            "created_at": now,
            "acked_at": None,
        }
        queue.append(row)
        queued.append(row)
    _set_dss_notifications(queue[-5000:])  # cap queue growth for local mode
    return queued


def _resolve_route_input(uav_id: str, route_id: Optional[str], waypoints: Optional[List[WaypointPayload]]) -> tuple[str, list[dict], str]:
    if isinstance(waypoints, list) and waypoints:
        return (str(route_id or "external-route"), [w.model_dump() for w in waypoints], "payload")
    rid, wps = _sim_waypoints(uav_id)
    return (str(route_id or rid), wps, "simulator")


def _route_volume4d_from_waypoints(waypoints: list[dict], *, planned_start_at: str | None = None, planned_end_at: str | None = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = planned_start_at or now.isoformat().replace("+00:00", "Z")
    end = planned_end_at or (now + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    if not waypoints:
        return {
            "x": [-1e9, 1e9],
            "y": [-1e9, 1e9],
            "z": [0.0, 120.0],
            "time_start": start,
            "time_end": end,
        }
    xs = [float(w.get("x", 0.0)) for w in waypoints]
    ys = [float(w.get("y", 0.0)) for w in waypoints]
    zs = [float(w.get("z", 0.0)) for w in waypoints]
    return {
        "x": [min(xs), max(xs)],
        "y": [min(ys), max(ys)],
        "z": [max(0.0, min(zs)), max(zs)],
        "time_start": start,
        "time_end": end,
    }


def _yaml_scalar(value: str) -> str:
    return str(value or "").strip().strip("\"'")


def _summarize_rtm(raw: str) -> Dict[str, Any]:
    requirement_ids: List[str] = []
    statuses: Dict[str, str] = {}
    current_id: str | None = None
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            current_id = _yaml_scalar(stripped.split(":", 1)[1])
            if current_id:
                requirement_ids.append(current_id)
            continue
        if stripped.startswith("status:") and current_id:
            statuses[current_id] = _yaml_scalar(stripped.split(":", 1)[1]).lower()

    status_counts: Dict[str, int] = {}
    for status in statuses.values():
        status_counts[status] = int(status_counts.get(status, 0)) + 1
    requirement_count = len(requirement_ids)
    implemented_count = int(status_counts.get("implemented", 0))
    partial_count = int(status_counts.get("partial", 0))
    coverage_pct = round(((implemented_count + (0.5 * partial_count)) / requirement_count) * 100.0, 1) if requirement_count else 0.0
    at_risk_ids = [rid for rid in requirement_ids if statuses.get(rid) in {"partial", "missing", "planned"}]
    return {
        "requirement_count": requirement_count,
        "status_counts": status_counts,
        "coverage_pct": coverage_pct,
        "at_risk_requirement_ids": at_risk_ids,
    }


def _load_rtm_artifact() -> Dict[str, Any]:
    if not RTM_PATH.exists():
        return {
            "path": str(RTM_PATH.relative_to(PROJECT_ROOT)),
            "present": False,
            "sha256": None,
            "summary": {"requirement_count": 0, "status_counts": {}, "coverage_pct": 0.0, "at_risk_requirement_ids": []},
            "raw": "",
        }
    raw = RTM_PATH.read_text(encoding="utf-8")
    return {
        "path": str(RTM_PATH.relative_to(PROJECT_ROOT)),
        "present": True,
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "summary": _summarize_rtm(raw),
        "raw": raw,
    }


def _build_compliance_export(*, limit_actions: int, include_action_payloads: bool, include_rtm_text: bool) -> Dict[str, Any]:
    conformance_last = UTM_DB.get_state("dss_conformance_last")
    intents = _get_dss_operational_intents()
    subscriptions = _get_dss_subscriptions()
    participants = _get_dss_participants()
    notifications = _get_dss_notifications()
    rtm = _load_rtm_artifact()
    actions = UTM_DB.recent_actions(limit_actions)

    evidence_index: List[Dict[str, Any]] = []
    for row in actions:
        if not isinstance(row, dict):
            continue
        result = row.get("result")
        item: Dict[str, Any] = {
            "action_id": row.get("id"),
            "evidence_id": str(row.get("action") or ""),
            "created_at": row.get("created_at"),
            "entity_id": row.get("entity_id"),
            "result_keys": sorted(list(result.keys())) if isinstance(result, dict) else [],
        }
        if include_action_payloads:
            item["payload"] = row.get("payload")
            item["result"] = result
        evidence_index.append(item)

    conformance = dict(conformance_last) if isinstance(conformance_last, dict) else None
    conformance_passed = bool(isinstance(conformance, dict) and conformance.get("passed") is True)
    requirement_count = int((rtm.get("summary") or {}).get("requirement_count", 0) or 0)
    rtm_coverage_pct = float((rtm.get("summary") or {}).get("coverage_pct", 0.0) or 0.0)
    readiness = "in_progress"
    if conformance_passed and requirement_count > 0 and rtm_coverage_pct >= 50.0:
        readiness = "pre_cert_candidate"

    return {
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": "utm_dss_gap_closure",
        "readiness": readiness,
        "artifacts": [
            {
                "artifact_id": "rtm",
                "type": "requirements_traceability_matrix",
                "path": rtm.get("path"),
                "present": bool(rtm.get("present")),
                "sha256": rtm.get("sha256"),
            },
            {
                "artifact_id": "dss_conformance_last",
                "type": "conformance_result",
                "present": conformance is not None,
                "generated_at": conformance.get("generated_at") if isinstance(conformance, dict) else None,
            },
            {
                "artifact_id": "utm_action_log",
                "type": "evidence_log",
                "present": True,
                "count": len(evidence_index),
            },
        ],
        "rtm": {
            "summary": rtm.get("summary"),
            "path": rtm.get("path"),
            "sha256": rtm.get("sha256"),
            "raw": rtm.get("raw") if include_rtm_text else None,
        },
        "conformance": conformance,
        "dss_state_snapshot": {
            "operational_intent_count": len(intents),
            "subscription_count": len(subscriptions),
            "participant_count": len(participants),
            "pending_notification_count": len([n for n in notifications if str(n.get("status", "")).lower() == "pending"]),
        },
        "evidence_index": evidence_index,
    }


def _safe_filename_token(value: Any, *, fallback: str) -> str:
    cleaned = "".join(ch if (ch.isalnum() or ch in {"-", "_", "."}) else "-" for ch in str(value or "").strip().lower())
    cleaned = cleaned.strip("-. _")
    return cleaned or fallback


def _render_certification_pack_summary_markdown(pack: Dict[str, Any]) -> str:
    profile = pack.get("jurisdiction_profile") if isinstance(pack.get("jurisdiction_profile"), dict) else {}
    summary = pack.get("summary") if isinstance(pack.get("summary"), dict) else {}
    safety_case = pack.get("safety_case") if isinstance(pack.get("safety_case"), dict) else {}
    critical = safety_case.get("critical_findings") if isinstance(safety_case.get("critical_findings"), list) else []
    cyber = pack.get("cyber_controls") if isinstance(pack.get("cyber_controls"), dict) else {}
    controls = cyber.get("controls") if isinstance(cyber.get("controls"), list) else []

    lines: List[str] = []
    lines.append(f"# Certification Pack Summary: {str(profile.get('name', profile.get('profile_id', '')))}")
    lines.append("")
    lines.append(f"- Exported At (UTC): `{datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}`")
    lines.append(f"- Pack ID: `{str(pack.get('pack_id', ''))}`")
    lines.append(f"- Profile ID: `{str(profile.get('profile_id', ''))}`")
    lines.append(f"- Profile Version: `{str(profile.get('version', ''))}`")
    lines.append(f"- Release ID: `{str(pack.get('release_id', ''))}`")
    lines.append(f"- Candidate Version: `{str(pack.get('candidate_version', ''))}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Release Ready: `{bool(summary.get('release_ready'))}`")
    lines.append(f"- Conformance Passed: `{bool(summary.get('conformance_passed'))}`")
    lines.append(f"- Total Claims: `{int(summary.get('total_claims', 0) or 0)}`")
    lines.append(f"- Supported Claims: `{int(summary.get('supported_claims', 0) or 0)}`")
    lines.append(f"- Partial Claims: `{int(summary.get('partial_claims', 0) or 0)}`")
    lines.append(f"- Gap Claims: `{int(summary.get('gap_claims', 0) or 0)}`")
    lines.append(f"- Critical Findings: `{int(summary.get('critical_findings', 0) or 0)}`")
    lines.append(f"- Missing Approvals: `{', '.join(summary.get('missing_approvals') or []) or '-'}`")
    lines.append("")
    lines.append("## Cyber Controls")
    lines.append("")
    lines.append("| Control ID | Status | Required Evidence | Matched Evidence |")
    lines.append("| --- | --- | --- | --- |")
    if controls:
        for raw in controls:
            row = raw if isinstance(raw, dict) else {}
            req = ", ".join(str(x) for x in (row.get("required_evidence_ids") or []))
            matched = ", ".join(str(x) for x in (row.get("matched_evidence_ids") or []))
            lines.append(f"| `{str(row.get('control_id', '-'))}` | `{str(row.get('status', '-'))}` | `{req or '-'}` | `{matched or '-'}` |")
    else:
        lines.append("| - | - | - | - |")
    lines.append("")
    lines.append("## Critical Findings")
    lines.append("")
    if critical:
        for raw in critical:
            finding = raw if isinstance(raw, dict) else {}
            lines.append(
                f"- `{str(finding.get('requirement_id', '-'))}` ({str(finding.get('phase', '-'))}): {str(finding.get('statement', ''))}"
            )
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines) + "\n"


def _resolve_project_relative_file(path_str: str) -> Optional[Path]:
    rel = Path(path_str)
    if rel.is_absolute():
        return None
    resolved = (PROJECT_ROOT / rel).resolve()
    root = PROJECT_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


@app.get("/api/utm/state")
def get_utm_state(airspace_segment: str = "sector-A3", operator_license_id: Optional[str] = None) -> Dict[str, Any]:
    dss_runtime = _dss_runtime_snapshot()
    dss_intents = dss_runtime.get("operationalIntents") if isinstance(dss_runtime.get("operationalIntents"), list) else []
    dss_subscriptions = dss_runtime.get("subscriptions") if isinstance(dss_runtime.get("subscriptions"), list) else []
    dss_participants = dss_runtime.get("participants") if isinstance(dss_runtime.get("participants"), list) else []
    dss_notifications = dss_runtime.get("notifications") if isinstance(dss_runtime.get("notifications"), list) else []
    weather_checks = UTM_SERVICE.check_weather(airspace_segment, operator_license_id=operator_license_id)
    fleet = _layered_fleet_snapshot()
    layered = build_layered_status(
        airspace_segment=airspace_segment,
        fleet=fleet,
        operational_intents=[dict(v) for v in dss_intents if isinstance(v, dict)],
        subscriptions=[dict(v) for v in dss_subscriptions if isinstance(v, dict)],
        participants=[dict(v) for v in dss_participants if isinstance(v, dict)],
        notifications=[dict(v) for v in dss_notifications if isinstance(v, dict)],
        weather_check=weather_checks,
        intents_adapter_mode=dss_runtime.get("intents_adapter_mode"),
        intents_degraded=dss_runtime.get("intents_degraded"),
        intents_failover_reason=dss_runtime.get("intents_failover_reason"),
        subscriptions_adapter_mode=dss_runtime.get("subscriptions_adapter_mode"),
        subscriptions_degraded=dss_runtime.get("subscriptions_degraded"),
        subscriptions_failover_reason=dss_runtime.get("subscriptions_failover_reason"),
    )
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "dataSource": _utm_source_info(),
        "result": {
            "airspace_segment": airspace_segment,
            "weather": UTM_SERVICE.get_weather(airspace_segment),
            "weatherChecks": weather_checks,
            "noFlyZones": list(UTM_SERVICE.no_fly_zones),
            "regulations": dict(UTM_SERVICE.regulations),
            "regulationProfiles": {k: dict(v) for k, v in UTM_SERVICE.regulation_profiles.items()},
            "effectiveRegulations": UTM_SERVICE.effective_regulations(operator_license_id),
            "selectedOperatorLicenseId": operator_license_id,
            "licenses": dict(UTM_SERVICE.operator_licenses),
            "dss": {
                "operationalIntentCount": len(dss_intents),
                "subscriptionCount": len(dss_subscriptions),
                "participantCount": len(dss_participants),
                "pendingNotificationCount": len([n for n in dss_notifications if isinstance(n, dict) and str(n.get("status")) == "pending"]),
                "operationalIntents": dss_intents,
                "subscriptions": dss_subscriptions,
                "participants": dss_participants,
                "intents_adapter_mode": dss_runtime.get("intents_adapter_mode"),
                "intents_degraded": dss_runtime.get("intents_degraded"),
                "intents_failover_reason": dss_runtime.get("intents_failover_reason"),
                "subscriptions_adapter_mode": dss_runtime.get("subscriptions_adapter_mode"),
                "subscriptions_degraded": dss_runtime.get("subscriptions_degraded"),
                "subscriptions_failover_reason": dss_runtime.get("subscriptions_failover_reason"),
            },
            "layeredStatus": layered,
        },
    }


@app.get("/api/utm/live/source")
def get_utm_live_source() -> Dict[str, Any]:
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": _utm_source_info()}


@app.post("/api/utm/live/ingest")
def post_utm_live_ingest(payload: UtmLiveIngestPayload) -> Dict[str, Any]:
    if isinstance(payload.weather, dict):
        UTM_SERVICE.set_weather(payload.airspace_segment, **payload.weather)
    if isinstance(payload.no_fly_zones, list):
        UTM_SERVICE.no_fly_zones = [dict(z) for z in payload.no_fly_zones if isinstance(z, dict)]
    if isinstance(payload.regulations, dict):
        UTM_SERVICE.regulations = dict(payload.regulations)
    if isinstance(payload.licenses, dict):
        UTM_SERVICE.operator_licenses = {str(k): dict(v) for k, v in payload.licenses.items() if isinstance(v, dict)}
    meta = {
        "source": payload.source,
        "source_ref": payload.source_ref,
        "observed_at": payload.observed_at,
        "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "airspace_segment": payload.airspace_segment,
    }
    UTM_DB.set_state("live_meta", meta)
    _persist_utm_state()
    sync = UTM_DB.record_action("utm_live_ingest", payload=payload.model_dump(), result={"dataSource": _utm_source_info()})
    return {"status": "success", "sync": sync, "result": {"dataSource": _utm_source_info()}}


@app.get("/api/utm/sync")
def get_utm_sync(limit_actions: int = 5) -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "sync": UTM_DB.get_sync(),
            "recentActions": UTM_DB.recent_actions(limit_actions),
        },
    }


@app.get("/api/utm/weather")
def get_weather(airspace_segment: str = "sector-A3") -> Dict[str, Any]:
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": UTM_SERVICE.check_weather(airspace_segment)}


@app.post("/api/utm/weather")
def set_weather(payload: WeatherPayload) -> Dict[str, Any]:
    weather = UTM_SERVICE.set_weather(
        payload.airspace_segment,
        wind_mps=payload.wind_mps,
        visibility_km=payload.visibility_km,
        precip_mmph=payload.precip_mmph,
        storm_alert=payload.storm_alert,
    )
    result = {"airspace_segment": payload.airspace_segment, "weather": weather}
    sync = _log_utm_action("set_weather", payload=payload.model_dump(), result=result, entity_id=payload.airspace_segment)
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/utm/nfz")
def list_no_fly_zones() -> Dict[str, Any]:
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"no_fly_zones": UTM_SERVICE.no_fly_zones}}


@app.post("/api/utm/nfz")
def add_no_fly_zone(payload: NoFlyZonePayload) -> Dict[str, Any]:
    result = UTM_SERVICE.add_no_fly_zone(**payload.model_dump())
    sync = _log_utm_action("add_no_fly_zone", payload=payload.model_dump(), result=result, entity_id=str(result.get("zone_id", "")))
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/license")
def register_license(payload: LicensePayload) -> Dict[str, Any]:
    result = UTM_SERVICE.register_operator_license(**payload.model_dump())
    sync = _log_utm_action("register_license", payload=payload.model_dump(), result=result, entity_id=payload.operator_license_id)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/corridor/reserve")
def reserve_corridor(payload: CorridorPayload) -> Dict[str, Any]:
    route_id, waypoints = _sim_waypoints(payload.uav_id)
    effective_route_id = str(payload.route_id or route_id)
    intents = _get_dss_operational_intents()
    volume4d = _route_volume4d_from_waypoints(waypoints)
    upsert = reserve_corridor_with_lease(
        intents,
        uav_id=payload.uav_id,
        airspace_segment=payload.airspace_segment,
        route_id=effective_route_id,
        volume4d=volume4d,
        manager_uss_id=payload.manager_uss_id,
        conflict_policy=payload.conflict_policy,
        lease_ttl_s=payload.lease_ttl_s,
        intent_id=f"corridor:{payload.uav_id}:{payload.airspace_segment}",
        metadata={
            "source": "reserve_corridor",
            "uav_id": payload.uav_id,
            "route_id": effective_route_id,
            "airspace_segment": payload.airspace_segment,
        },
    )
    if upsert.get("stored"):
        _set_dss_operational_intents(intents)
    intent = upsert.get("intent") if isinstance(upsert.get("intent"), dict) else {}
    event_type = "create" if int(intent.get("version", 1) or 1) <= 1 else "update"
    subscriptions = _get_dss_subscriptions()
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=intent.get("volume4d") if isinstance(intent.get("volume4d"), dict) else volume4d,
            event_type=event_type,
        )
        if upsert.get("stored")
        else []
    )
    queued_notifications = _queue_dss_notifications(
        notifications=notifications,
        event_type=event_type,
        source_intent_id=str(intent.get("intent_id") or ""),
    )
    result = {
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "route_id": effective_route_id,
        "reserved": bool(upsert.get("stored")),
        "reservation_id": upsert.get("reservation_id"),
        "lease": upsert.get("lease"),
        "intent_graph": upsert.get("intent_graph"),
        "intent_result": upsert,
        "subscriptions_to_notify": notifications,
        "queued_notifications": queued_notifications,
    }
    sync = _log_utm_action("reserve_corridor", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/utm/dss/state")
def get_dss_state() -> Dict[str, Any]:
    dss_runtime = _dss_runtime_snapshot()
    intents = dss_runtime.get("operationalIntents") if isinstance(dss_runtime.get("operationalIntents"), list) else []
    subscriptions = dss_runtime.get("subscriptions") if isinstance(dss_runtime.get("subscriptions"), list) else []
    participants = dss_runtime.get("participants") if isinstance(dss_runtime.get("participants"), list) else []
    notifications = dss_runtime.get("notifications") if isinstance(dss_runtime.get("notifications"), list) else []
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "operationalIntentCount": len(intents),
            "subscriptionCount": len(subscriptions),
            "participantCount": len(participants),
            "notificationCount": len(notifications),
            "pendingNotificationCount": len([n for n in notifications if isinstance(n, dict) and str(n.get("status")) == "pending"]),
            "deliveredNotificationCount": len([n for n in notifications if isinstance(n, dict) and str(n.get("status")) == "delivered"]),
            "failedNotificationCount": len([n for n in notifications if isinstance(n, dict) and str(n.get("status")) == "failed"]),
            "operationalIntents": intents,
            "subscriptions": subscriptions,
            "participants": participants,
            "notifications": notifications[-50:],
            "intents_adapter_mode": dss_runtime.get("intents_adapter_mode"),
            "intents_degraded": dss_runtime.get("intents_degraded"),
            "intents_failover_reason": dss_runtime.get("intents_failover_reason"),
            "subscriptions_adapter_mode": dss_runtime.get("subscriptions_adapter_mode"),
            "subscriptions_degraded": dss_runtime.get("subscriptions_degraded"),
            "subscriptions_failover_reason": dss_runtime.get("subscriptions_failover_reason"),
        },
    }


@app.get("/api/utm/layers/status")
def get_layered_status(airspace_segment: str = "sector-A3", operator_license_id: Optional[str] = None) -> Dict[str, Any]:
    dss_runtime = _dss_runtime_snapshot()
    intents = dss_runtime.get("operationalIntents") if isinstance(dss_runtime.get("operationalIntents"), list) else []
    subscriptions = dss_runtime.get("subscriptions") if isinstance(dss_runtime.get("subscriptions"), list) else []
    participants = dss_runtime.get("participants") if isinstance(dss_runtime.get("participants"), list) else []
    notifications = dss_runtime.get("notifications") if isinstance(dss_runtime.get("notifications"), list) else []
    weather_checks = UTM_SERVICE.check_weather(airspace_segment, operator_license_id=operator_license_id)
    fleet = _layered_fleet_snapshot()
    result = build_layered_status(
        airspace_segment=airspace_segment,
        fleet=fleet,
        operational_intents=[dict(v) for v in intents if isinstance(v, dict)],
        subscriptions=[dict(v) for v in subscriptions if isinstance(v, dict)],
        participants=[dict(v) for v in participants if isinstance(v, dict)],
        notifications=[dict(v) for v in notifications if isinstance(v, dict)],
        weather_check=weather_checks,
        intents_adapter_mode=dss_runtime.get("intents_adapter_mode"),
        intents_degraded=dss_runtime.get("intents_degraded"),
        intents_failover_reason=dss_runtime.get("intents_failover_reason"),
        subscriptions_adapter_mode=dss_runtime.get("subscriptions_adapter_mode"),
        subscriptions_degraded=dss_runtime.get("subscriptions_degraded"),
        subscriptions_failover_reason=dss_runtime.get("subscriptions_failover_reason"),
    )
    return {"status": "success", "sync": UTM_DB.get_sync(), "dataSource": _utm_source_info(), "result": result}


def _upsert_operational_intent_common(
    payload: OperationalIntentPayload,
    *,
    forced_intent_id: Optional[str] = None,
    action_name: str = "dss_upsert_operational_intent",
) -> Dict[str, Any]:
    resolved_intent_id = str(forced_intent_id).strip() if forced_intent_id is not None else payload.intent_id
    payload_dict = payload.model_dump()
    if resolved_intent_id:
        payload_dict["intent_id"] = resolved_intent_id
    out = gateway_upsert_operational_intent(UTM_DB, payload_dict)
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    upsert = out_result.get("upsert") if isinstance(out_result.get("upsert"), dict) else out_result
    if not isinstance(upsert, dict):
        upsert = {}
    intent = upsert.get("intent") if isinstance(upsert.get("intent"), dict) else {}
    notifications = (
        out_result.get("subscriptions_to_notify")
        if isinstance(out_result.get("subscriptions_to_notify"), list)
        else []
    )
    queued_notifications = (
        out_result.get("queued_notifications")
        if isinstance(out_result.get("queued_notifications"), list)
        else []
    )
    intents = _get_dss_operational_intents()
    subscriptions = _get_dss_subscriptions()
    result = {
        "upsert": upsert,
        "signature_verification": out_result.get("signature_verification"),
        "subscriptions_to_notify": notifications,
        "queued_notifications": queued_notifications,
        "counts": {"operationalIntents": len(intents), "subscriptions": len(subscriptions)},
        "adapter_mode": out.get("adapter_mode"),
        "degraded": out.get("degraded"),
        "failover_reason": out.get("failover_reason"),
    }
    sync = _log_utm_action(
        action_name,
        payload=payload_dict,
        result={
            "status": upsert.get("status"),
            "stored": upsert.get("stored"),
            "intent_id": intent.get("intent_id"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": bool(out.get("degraded")),
        },
        entity_id=str(intent.get("intent_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/dss/operational-intents")
def post_operational_intent(payload: OperationalIntentPayload) -> Dict[str, Any]:
    return _upsert_operational_intent_common(payload, forced_intent_id=None, action_name="dss_upsert_operational_intent")


@app.put("/api/utm/dss/operational-intents/{intent_id}")
def put_operational_intent(intent_id: str, payload: OperationalIntentPayload) -> Dict[str, Any]:
    return _upsert_operational_intent_common(payload, forced_intent_id=intent_id, action_name="dss_put_operational_intent")


@app.post("/api/utm/dss/operational-intents/query")
def post_query_operational_intents(payload: OperationalIntentQueryPayload) -> Dict[str, Any]:
    out = gateway_query_operational_intents(
        UTM_DB,
        {
            "manager_uss_id": payload.manager_uss_id,
            "states": payload.states,
            "volume4d": payload.volume4d.as_volume4d() if isinstance(payload.volume4d, Volume4DPayload) else None,
        },
    )
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    items = out_result.get("items") if isinstance(out_result.get("items"), list) else []
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "query": payload.model_dump(),
            "count": len(items),
            "items": items,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.get("/api/utm/dss/operational-intents/query")
def get_query_operational_intents(
    manager_uss_id: Optional[str] = None,
    states: Optional[str] = None,
) -> Dict[str, Any]:
    parsed_states = [s.strip() for s in str(states or "").split(",") if s.strip()]
    out = gateway_query_operational_intents(
        UTM_DB,
        {"manager_uss_id": manager_uss_id, "states": parsed_states or None, "volume4d": None},
    )
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    items = out_result.get("items") if isinstance(out_result.get("items"), list) else []
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "query": {"manager_uss_id": manager_uss_id, "states": parsed_states},
            "count": len(items),
            "items": items,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.delete("/api/utm/dss/operational-intents/{intent_id}")
def delete_operational_intent(intent_id: str) -> Dict[str, Any]:
    out = gateway_delete_operational_intent(UTM_DB, intent_id)
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    result = out_result.get("result") if isinstance(out_result.get("result"), dict) else out_result
    if not isinstance(result, dict):
        result = {}
    notifications = (
        out_result.get("subscriptions_to_notify")
        if isinstance(out_result.get("subscriptions_to_notify"), list)
        else []
    )
    queued_notifications = (
        out_result.get("queued_notifications")
        if isinstance(out_result.get("queued_notifications"), list)
        else []
    )
    sync = _log_utm_action(
        "dss_delete_operational_intent",
        payload={"intent_id": intent_id},
        result={
            "deleted": result.get("deleted"),
            "intent_id": intent_id,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": bool(out.get("degraded")),
        },
        entity_id=intent_id,
    )
    return {
        "status": "success",
        "sync": sync,
        "result": {
            **result,
            "subscriptions_to_notify": notifications,
            "queued_notifications": queued_notifications,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.post("/api/utm/dss/subscriptions")
def post_subscription(payload: SubscriptionPayload) -> Dict[str, Any]:
    payload_dict = payload.model_dump()
    out = gateway_upsert_subscription(UTM_DB, payload_dict)
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    result = out_result.get("result") if isinstance(out_result.get("result"), dict) else out_result
    if not isinstance(result, dict):
        result = {}
    sub = result.get("subscription") if isinstance(result.get("subscription"), dict) else {}
    sync = _log_utm_action(
        "dss_upsert_subscription",
        payload=payload_dict,
        result={
            "subscription_id": sub.get("subscription_id"),
            "manager_uss_id": sub.get("manager_uss_id"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": bool(out.get("degraded")),
        },
        entity_id=str(sub.get("subscription_id") or ""),
    )
    return {
        "status": "success",
        "sync": sync,
        "result": {
            **result,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.post("/api/utm/dss/subscriptions/query")
def post_query_subscriptions(payload: SubscriptionQueryPayload) -> Dict[str, Any]:
    out = gateway_query_subscriptions(
        UTM_DB,
        {
            "manager_uss_id": payload.manager_uss_id,
            "volume4d": payload.volume4d.as_volume4d() if isinstance(payload.volume4d, Volume4DPayload) else None,
        },
    )
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    items = out_result.get("items") if isinstance(out_result.get("items"), list) else []
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "query": payload.model_dump(),
            "count": len(items),
            "items": items,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.get("/api/utm/dss/subscriptions")
def get_subscriptions(manager_uss_id: Optional[str] = None) -> Dict[str, Any]:
    out = gateway_query_subscriptions(UTM_DB, {"manager_uss_id": manager_uss_id, "volume4d": None})
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    items = out_result.get("items") if isinstance(out_result.get("items"), list) else []
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "query": {"manager_uss_id": manager_uss_id},
            "count": len(items),
            "items": items,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.delete("/api/utm/dss/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: str) -> Dict[str, Any]:
    out = gateway_delete_subscription(UTM_DB, subscription_id)
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error"),
            "details": out.get("details"),
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
        }
    out_result = out.get("result") if isinstance(out.get("result"), dict) else {}
    result = out_result.get("result") if isinstance(out_result.get("result"), dict) else out_result
    if not isinstance(result, dict):
        result = {}
    sync = _log_utm_action(
        "dss_delete_subscription",
        payload={"subscription_id": subscription_id},
        result={
            "deleted": result.get("deleted"),
            "subscription_id": subscription_id,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": bool(out.get("degraded")),
        },
        entity_id=subscription_id,
    )
    return {
        "status": "success",
        "sync": sync,
        "result": {
            **result,
            "adapter_mode": out.get("adapter_mode"),
            "degraded": out.get("degraded"),
            "failover_reason": out.get("failover_reason"),
        },
    }


@app.get("/api/utm/dss/participants")
def get_participants() -> Dict[str, Any]:
    participants = _get_dss_participants()
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {"count": len(participants), "items": list(participants.values())},
    }


@app.post("/api/utm/dss/participants")
def post_participant(payload: ParticipantPayload) -> Dict[str, Any]:
    participants = _get_dss_participants()
    pid = str(payload.participant_id).strip()
    if not pid:
        return {"status": "error", "error": "participant_id_required"}
    prev = participants.get(pid) if isinstance(participants.get(pid), dict) else {}
    row = {
        "participant_id": pid,
        "uss_base_url": str(payload.uss_base_url or ""),
        "roles": [str(r).strip().lower() for r in payload.roles if str(r).strip()],
        "status": str(payload.status or "active").strip().lower() or "active",
        "metadata": dict(payload.metadata or {}),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": int(prev.get("version", 0) or 0) + 1 if isinstance(prev, dict) else 1,
    }
    participants[pid] = row
    _set_dss_participants(participants)
    sync = _log_utm_action("dss_upsert_participant", payload=payload.model_dump(), result={"participant_id": pid}, entity_id=pid)
    return {"status": "success", "sync": sync, "result": row}


@app.delete("/api/utm/dss/participants/{participant_id}")
def delete_participant(participant_id: str) -> Dict[str, Any]:
    participants = _get_dss_participants()
    existed = participants.pop(participant_id, None)
    _set_dss_participants(participants)
    sync = _log_utm_action(
        "dss_delete_participant",
        payload={"participant_id": participant_id},
        result={"deleted": existed is not None},
        entity_id=participant_id,
    )
    return {
        "status": "success",
        "sync": sync,
        "result": {"deleted": existed is not None, "participant_id": participant_id, "participant": existed},
    }


@app.get("/api/utm/dss/notifications")
def get_notifications(
    limit: int = 100,
    status: Optional[str] = None,
    subscription_id: Optional[str] = None,
) -> Dict[str, Any]:
    notifications = _get_dss_notifications()
    filtered = notifications
    if status:
        filtered = [n for n in filtered if str(n.get("status", "")).lower() == str(status).lower()]
    if subscription_id:
        filtered = [n for n in filtered if str(n.get("subscription_id", "")) == str(subscription_id)]
    limit_n = max(1, min(1000, int(limit)))
    items = list(reversed(filtered))[:limit_n]
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {"count": len(items), "items": items},
    }


@app.post("/api/utm/dss/notifications/{notification_id}/ack")
def ack_notification(notification_id: str) -> Dict[str, Any]:
    notifications = _get_dss_notifications()
    found = False
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for rec in notifications:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("notification_id", "")) != str(notification_id):
            continue
        rec["status"] = "acked"
        rec["acked_at"] = now
        found = True
        break
    _set_dss_notifications(notifications)
    sync = _log_utm_action(
        "dss_ack_notification",
        payload={"notification_id": notification_id},
        result={"acked": found},
        entity_id=notification_id,
    )
    return {"status": "success", "sync": sync, "result": {"acked": found, "notification_id": notification_id}}


@app.post("/api/utm/dss/notifications/dispatch")
def dispatch_notifications(payload: Optional[DssNotificationDispatchPayload] = None) -> Dict[str, Any]:
    run_limit = 1
    if isinstance(payload, DssNotificationDispatchPayload):
        try:
            run_limit = int(payload.run_limit)
        except Exception:
            run_limit = 1
    run_limit = max(1, min(50, run_limit))

    aggregate = {"attempted": 0, "delivered": 0, "failed": 0}
    rounds = 0
    while rounds < run_limit:
        rounds += 1
        out = _dispatch_pending_dss_notifications_once()
        aggregate["attempted"] += int(out.get("attempted", 0) or 0)
        aggregate["delivered"] += int(out.get("delivered", 0) or 0)
        aggregate["failed"] += int(out.get("failed", 0) or 0)
        if int(out.get("attempted", 0) or 0) <= 0:
            break

    counts = _dss_notification_counts()
    result = {
        "run_limit": run_limit,
        "rounds": rounds,
        "dispatcher_enabled": _dss_dispatcher_enabled(),
        "summary": aggregate,
        "counts": counts,
    }
    sync = _log_utm_action(
        "dss_dispatch_notifications_manual",
        payload={"run_limit": run_limit},
        result=result,
        entity_id="dss_notifications",
    )
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/dss/notifications/dispatch/enabled")
def set_dispatch_notifications_enabled(payload: DssNotificationDispatcherEnabledPayload) -> Dict[str, Any]:
    global _DSS_DISPATCH_RUNTIME_ENABLED
    enabled = bool(payload.enabled)
    _DSS_DISPATCH_RUNTIME_ENABLED = enabled
    if enabled:
        _start_dss_notification_dispatcher()
    else:
        _stop_dss_notification_dispatcher()
    thread_alive = bool(isinstance(_DSS_DISPATCH_THREAD, threading.Thread) and _DSS_DISPATCH_THREAD.is_alive())
    result = {
        "dispatcher_enabled": _dss_dispatcher_enabled(),
        "configured_enabled": _dss_dispatcher_config_enabled(),
        "runtime_override_enabled": _DSS_DISPATCH_RUNTIME_ENABLED,
        "worker_thread_alive": thread_alive,
        "worker_stop_requested": bool(_DSS_DISPATCH_STOP.is_set()),
    }
    sync = _log_utm_action(
        "dss_dispatcher_set_enabled",
        payload={"enabled": enabled},
        result=result,
        entity_id="dss_notifications",
    )
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/utm/dss/notifications/dispatch/status")
def get_dispatch_notifications_status() -> Dict[str, Any]:
    thread_alive = bool(isinstance(_DSS_DISPATCH_THREAD, threading.Thread) and _DSS_DISPATCH_THREAD.is_alive())
    with _DSS_DISPATCH_LOCK:
        last_cycle = dict(_DSS_DISPATCH_LAST_CYCLE)
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "dispatcher_enabled": _dss_dispatcher_enabled(),
            "configured_enabled": _dss_dispatcher_config_enabled(),
            "runtime_override_enabled": _DSS_DISPATCH_RUNTIME_ENABLED,
            "worker_thread_alive": thread_alive,
            "worker_stop_requested": bool(_DSS_DISPATCH_STOP.is_set()),
            "config": {
                "interval_s": _dss_dispatch_interval_s(),
                "timeout_s": _dss_dispatch_timeout_s(),
                "batch_size": _dss_dispatch_batch_size(),
                "max_attempts": _dss_dispatch_max_attempts(),
            },
            "last_cycle": last_cycle,
            "counts": _dss_notification_counts(),
        },
    }


@app.post("/api/utm/conformance/run-local")
def run_local_conformance(payload: ConformanceRunPayload) -> Dict[str, Any]:
    intents = {} if payload.reset_before_run else _get_dss_operational_intents()
    subscriptions = {} if payload.reset_before_run else _get_dss_subscriptions()

    scenarios: List[Dict[str, Any]] = []

    # Scenario 1: non-overlap intents should both be stored.
    a = dss_upsert_intent(
        intents,
        intent_id="conf-a",
        manager_uss_id="uss-a",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d={"x": [0, 50], "y": [0, 50], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
        metadata={"scenario": "local_conformance"},
    )
    b = dss_upsert_intent(
        intents,
        intent_id="conf-b",
        manager_uss_id="uss-b",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d={"x": [60, 90], "y": [60, 90], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
        metadata={"scenario": "local_conformance"},
    )
    pass_non_overlap = bool(a.get("stored") and b.get("stored"))
    scenarios.append({"scenario": "non_overlap_intents", "passed": pass_non_overlap, "details": {"a": a.get("status"), "b": b.get("status")}})

    # Scenario 2: overlap + reject should fail storage.
    c = dss_upsert_intent(
        intents,
        intent_id="conf-c",
        manager_uss_id="uss-c",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d={"x": [40, 70], "y": [40, 70], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
        metadata={"scenario": "local_conformance"},
    )
    pass_overlap_reject = bool(c.get("status") == "rejected" and not c.get("stored"))
    scenarios.append({"scenario": "overlap_reject", "passed": pass_overlap_reject, "details": {"status": c.get("status"), "blocking": len(c.get("blocking_conflicts") or [])}})

    # Scenario 3: subscription should receive queued notification on create.
    sub = dss_upsert_subscription(
        subscriptions,
        subscription_id="conf-sub-1",
        manager_uss_id="uss-watch",
        callback_url="local://uss-watch/callback",
        volume4d={"x": [0, 100], "y": [0, 100], "z": [0, 120], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T13:00:00Z"},
        notify_for=["create", "update", "delete"],
    )
    d = dss_upsert_intent(
        intents,
        intent_id="conf-d",
        manager_uss_id="uss-d",
        state="accepted",
        priority="high",
        conflict_policy="conditional_approve",
        volume4d={"x": [10, 20], "y": [10, 20], "z": [0, 50], "time_start": "2026-02-27T12:05:00Z", "time_end": "2026-02-27T12:30:00Z"},
    )
    impacted = dss_impacted_subscriptions(
        subscriptions,
        changed_volume4d=(d.get("intent") or {}).get("volume4d", {"x": [0, 0], "y": [0, 0], "z": [0, 0], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:01:00Z"}),
        event_type="create",
    )
    queued = _queue_dss_notifications(notifications=impacted, event_type="create", source_intent_id="conf-d")
    pass_notification = bool(sub.get("status") == "success" and len(queued) >= 1)
    scenarios.append({"scenario": "subscription_notification_queue", "passed": pass_notification, "details": {"queued": len(queued)}})

    if payload.reset_before_run:
        _set_dss_operational_intents(intents)
        _set_dss_subscriptions(subscriptions)

    passed = all(bool(s.get("passed")) for s in scenarios)
    result = {
        "passed": passed,
        "total": len(scenarios),
        "passed_count": len([s for s in scenarios if s.get("passed")]),
        "scenarios": scenarios,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    UTM_DB.set_state("dss_conformance_last", result)
    sync = _log_utm_action("dss_run_local_conformance", payload=payload.model_dump(), result={"passed": passed, "total": len(scenarios)})
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/utm/conformance/last")
def get_last_conformance() -> Dict[str, Any]:
    raw = UTM_DB.get_state("dss_conformance_last")
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": dict(raw) if isinstance(raw, dict) else None}


@app.get("/api/utm/compliance/export")
def get_compliance_export(
    limit_actions: int = 50,
    include_action_payloads: bool = False,
    include_rtm_text: bool = False,
) -> Dict[str, Any]:
    limit = max(1, min(100, int(limit_actions)))
    pack = _build_compliance_export(
        limit_actions=limit,
        include_action_payloads=bool(include_action_payloads),
        include_rtm_text=bool(include_rtm_text),
    )
    sync = _log_utm_action(
        "compliance_export",
        payload={"limit_actions": limit, "include_action_payloads": bool(include_action_payloads), "include_rtm_text": bool(include_rtm_text)},
        result={
            "artifact_count": len(pack.get("artifacts") or []),
            "evidence_count": len(pack.get("evidence_index") or []),
            "readiness": pack.get("readiness"),
        },
    )
    return {"status": "success", "sync": sync, "result": pack}


@app.get("/api/utm/certification/profiles")
def get_certification_profiles() -> Dict[str, Any]:
    profile_ids = list_available_profiles(PROFILES_DIR)
    items: List[Dict[str, Any]] = []
    for pid in profile_ids:
        try:
            profile = load_jurisdiction_profile(pid, PROFILES_DIR)
        except Exception:
            continue
        items.append(
            {
                "profile_id": profile.get("profile_id"),
                "name": profile.get("name"),
                "version": profile.get("version"),
                "effective_date": profile.get("effective_date"),
                "source_path": profile.get("source_path"),
            }
        )
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"count": len(items), "items": items}}


@app.post("/api/utm/certification/pack/generate")
def generate_certification_pack(payload: CertificationPackGeneratePayload) -> Dict[str, Any]:
    try:
        profile = load_jurisdiction_profile(payload.jurisdiction_profile, PROFILES_DIR)
    except FileNotFoundError:
        return {"status": "error", "error": "jurisdiction_profile_not_found", "profile_id": payload.jurisdiction_profile}
    except Exception as exc:
        return {"status": "error", "error": "jurisdiction_profile_invalid", "details": str(exc)}

    rtm = _load_rtm_artifact()
    requirements = parse_rtm_requirements(str(rtm.get("raw", "") or ""))
    evidence_limit = max(1, min(500, int(payload.evidence_limit_actions)))
    compliance = _build_compliance_export(
        limit_actions=evidence_limit,
        include_action_payloads=bool(payload.include_action_payloads),
        include_rtm_text=bool(payload.include_rtm_text),
    )
    evidence_index = list(compliance.get("evidence_index") or []) if isinstance(compliance.get("evidence_index"), list) else []
    conformance = compliance.get("conformance") if isinstance(compliance.get("conformance"), dict) else None
    approvals = [a.model_dump() for a in (payload.approvals or [])]

    pack = build_certification_pack(
        profile=profile,
        rtm_requirements=requirements,
        conformance=conformance,
        evidence_index=evidence_index,
        release_id=payload.release_id,
        candidate_version=payload.candidate_version,
        approvals=approvals,
        notes=payload.notes,
    )
    pack["source_artifacts"] = {
        "rtm_path": rtm.get("path"),
        "rtm_sha256": rtm.get("sha256"),
        "conformance_generated_at": conformance.get("generated_at") if isinstance(conformance, dict) else None,
        "evidence_count": len(evidence_index),
    }
    if bool(payload.include_rtm_text):
        pack["rtm_text"] = rtm.get("raw", "")

    packs = _get_certification_packs()
    packs[str(pack.get("pack_id"))] = pack
    if len(packs) > 200:
        rows = [v for v in packs.values() if isinstance(v, dict)]
        rows.sort(key=lambda x: str(x.get("generated_at") or ""), reverse=True)
        keep_ids = {str(v.get("pack_id")) for v in rows[:200] if str(v.get("pack_id") or "").strip()}
        packs = {k: v for k, v in packs.items() if k in keep_ids}
    _set_certification_packs(packs)

    sync = _log_utm_action(
        "certification_pack_generate",
        payload=payload.model_dump(),
        result={
            "pack_id": pack.get("pack_id"),
            "profile_id": profile.get("profile_id"),
            "release_ready": ((pack.get("summary") or {}).get("release_ready") if isinstance(pack.get("summary"), dict) else False),
            "critical_findings": ((pack.get("summary") or {}).get("critical_findings") if isinstance(pack.get("summary"), dict) else 0),
        },
        entity_id=str(pack.get("pack_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": pack}


@app.get("/api/utm/certification/pack/{pack_id}")
def get_certification_pack(pack_id: str) -> Dict[str, Any]:
    packs = _get_certification_packs()
    row = packs.get(pack_id) if isinstance(packs.get(pack_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "certification_pack_not_found", "pack_id": pack_id}
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": row}


@app.post("/api/utm/certification/pack/{pack_id}/export-docs")
def export_certification_pack_docs(pack_id: str) -> Dict[str, Any]:
    packs = _get_certification_packs()
    pack = packs.get(pack_id) if isinstance(packs.get(pack_id), dict) else None
    if not isinstance(pack, dict):
        return {"status": "error", "error": "certification_pack_not_found", "pack_id": pack_id}

    profile = pack.get("jurisdiction_profile") if isinstance(pack.get("jurisdiction_profile"), dict) else {}
    profile_id = _safe_filename_token(profile.get("profile_id"), fallback="profile")
    pack_token = _safe_filename_token(pack.get("pack_id"), fallback="pack")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    export_id = f"cert-exp-{uuid4().hex[:12]}"

    export_dir = CERTIFICATION_EXPORTS_DIR / profile_id
    export_dir.mkdir(parents=True, exist_ok=True)
    pack_path = export_dir / f"{stamp}_{pack_token}_pack.json"
    summary_path = export_dir / f"{stamp}_{pack_token}_summary.md"

    pack_path.write_text(json.dumps(pack, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    summary_path.write_text(_render_certification_pack_summary_markdown(pack), encoding="utf-8")

    pack_rel = str(pack_path.relative_to(PROJECT_ROOT))
    summary_rel = str(summary_path.relative_to(PROJECT_ROOT))
    exported_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "export_id": export_id,
        "pack_id": str(pack.get("pack_id") or ""),
        "profile_id": str(profile.get("profile_id") or ""),
        "release_id": str(pack.get("release_id") or ""),
        "candidate_version": str(pack.get("candidate_version") or ""),
        "exported_at": exported_at,
        "export_dir": str(export_dir.relative_to(PROJECT_ROOT)),
        "downloads": [
            {
                "kind": "pack_json",
                "label": "Pack JSON",
                "filename": pack_path.name,
                "relative_path": pack_rel,
                "download_path": f"/api/utm/certification/exports/{export_id}/download/pack",
                "content_type": "application/json",
            },
            {
                "kind": "summary_markdown",
                "label": "Summary Markdown",
                "filename": summary_path.name,
                "relative_path": summary_rel,
                "download_path": f"/api/utm/certification/exports/{export_id}/download/summary",
                "content_type": "text/markdown; charset=utf-8",
            },
        ],
        "release_ready": bool(((pack.get("summary") or {}).get("release_ready")) if isinstance(pack.get("summary"), dict) else False),
        "critical_findings": int(
            (((pack.get("summary") or {}).get("critical_findings")) if isinstance(pack.get("summary"), dict) else 0) or 0
        ),
    }

    exports = _get_certification_doc_exports()
    exports[export_id] = record
    rows = [v for v in exports.values() if isinstance(v, dict)]
    rows.sort(key=lambda x: str(x.get("exported_at") or ""), reverse=True)
    if len(rows) > 200:
        keep = {str(v.get("export_id")) for v in rows[:200] if str(v.get("export_id") or "").strip()}
        exports = {k: v for k, v in exports.items() if k in keep}
    _set_certification_doc_exports(exports)

    sync = _log_utm_action(
        "certification_docs_export",
        payload={"pack_id": pack_id},
        result={"export_id": export_id, "profile_id": profile_id, "export_dir": str(export_dir.relative_to(PROJECT_ROOT))},
        entity_id=export_id,
    )
    return {"status": "success", "sync": sync, "result": record}


@app.get("/api/utm/certification/exports")
def list_certification_doc_exports(limit: int = 30) -> Dict[str, Any]:
    max_items = max(1, min(200, int(limit)))
    rows = [v for v in _get_certification_doc_exports().values() if isinstance(v, dict)]
    rows.sort(key=lambda x: str(x.get("exported_at") or ""), reverse=True)
    items = rows[:max_items]
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"count": len(items), "items": items}}


@app.get("/api/utm/certification/exports/{export_id}/download/{artifact}")
def download_certification_doc_export(export_id: str, artifact: str):
    artifact_key = str(artifact or "").strip().lower()
    if artifact_key not in {"pack", "summary"}:
        return {"status": "error", "error": "unsupported_export_artifact", "artifact": artifact_key}

    exports = _get_certification_doc_exports()
    record = exports.get(export_id) if isinstance(exports.get(export_id), dict) else None
    if not isinstance(record, dict):
        return {"status": "error", "error": "certification_export_not_found", "export_id": export_id}

    expected_kind = "pack_json" if artifact_key == "pack" else "summary_markdown"
    downloads = record.get("downloads") if isinstance(record.get("downloads"), list) else []
    row = next((d for d in downloads if isinstance(d, dict) and str(d.get("kind")) == expected_kind), None)
    if not isinstance(row, dict):
        return {"status": "error", "error": "certification_export_artifact_not_found", "export_id": export_id, "artifact": artifact_key}

    relative_path = str(row.get("relative_path") or "")
    file_path = _resolve_project_relative_file(relative_path)
    if file_path is None:
        return {"status": "error", "error": "certification_export_path_invalid", "export_id": export_id}
    if not file_path.exists():
        return {"status": "error", "error": "certification_export_file_missing", "export_id": export_id, "path": relative_path}

    filename = str(row.get("filename") or file_path.name)
    media_type = "application/json" if expected_kind == "pack_json" else "text/markdown; charset=utf-8"
    return FileResponse(path=str(file_path), media_type=media_type, filename=filename)


@app.post("/api/utm/release/gate/evaluate")
def post_release_gate_evaluate(payload: ReleaseGateEvaluatePayload) -> Dict[str, Any]:
    packs = _get_certification_packs()
    pack = packs.get(str(payload.pack_id)) if payload.pack_id and isinstance(packs.get(str(payload.pack_id)), dict) else None
    deviations = [v for v in _get_release_deviations().values() if isinstance(v, dict)]
    latest_conformance = UTM_DB.get_state("dss_conformance_last")
    latest_conformance_dict = dict(latest_conformance) if isinstance(latest_conformance, dict) else None

    campaign_report = None
    if payload.campaign_id:
        campaigns = _get_interop_campaigns()
        camp = campaigns.get(str(payload.campaign_id)) if isinstance(campaigns.get(str(payload.campaign_id)), dict) else None
        if isinstance(camp, dict):
            compliance = _build_compliance_export(limit_actions=100, include_action_payloads=False, include_rtm_text=False)
            campaign_report = build_campaign_report(camp, compliance_export=compliance)

    gate = evaluate_release_gate(
        release_id=payload.release_id,
        pack=pack,
        required_approvals=payload.required_approvals,
        deviations=deviations,
        latest_conformance=latest_conformance_dict,
        require_signed_campaign_report=payload.require_signed_campaign_report,
        campaign_report=campaign_report,
        enforce_critical_findings=payload.enforce_critical_findings,
    )
    evaluations = _get_release_gate_evaluations()
    evaluations[str(gate.get("gate_id"))] = gate
    _set_release_gate_evaluations(evaluations)

    sync = _log_utm_action(
        "release_gate_evaluate",
        payload=payload.model_dump(),
        result={"gate_id": gate.get("gate_id"), "decision": gate.get("decision"), "reasons": gate.get("reasons")},
        entity_id=str(gate.get("gate_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": gate}


@app.post("/api/utm/release/deviations")
def post_release_deviation(payload: DeviationPayload) -> Dict[str, Any]:
    row = create_deviation_record(
        release_id=payload.release_id,
        category=payload.category,
        severity=payload.severity,
        description=payload.description,
        rationale=str(payload.rationale or ""),
        mitigation_plan=str(payload.mitigation_plan or ""),
        owner=payload.owner,
        status=payload.status,
        metadata=payload.metadata,
    )
    deviations = _get_release_deviations()
    deviations[str(row.get("deviation_id"))] = row
    _set_release_deviations(deviations)
    sync = _log_utm_action(
        "release_deviation_create",
        payload=payload.model_dump(),
        result={"deviation_id": row.get("deviation_id"), "severity": row.get("severity"), "status": row.get("status")},
        entity_id=str(row.get("deviation_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": row}


@app.post("/api/utm/release/deviations/{deviation_id}/resolve")
def post_release_deviation_resolve(deviation_id: str, payload: DeviationResolvePayload) -> Dict[str, Any]:
    deviations = _get_release_deviations()
    row = deviations.get(deviation_id) if isinstance(deviations.get(deviation_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "deviation_not_found", "deviation_id": deviation_id}
    resolved = resolve_deviation_record(
        row,
        status=payload.status,
        resolver=payload.resolver,
        resolution_note=str(payload.resolution_note or ""),
    )
    deviations[deviation_id] = resolved
    _set_release_deviations(deviations)
    sync = _log_utm_action(
        "release_deviation_resolve",
        payload={"deviation_id": deviation_id, **payload.model_dump()},
        result={"deviation_id": deviation_id, "status": resolved.get("status")},
        entity_id=deviation_id,
    )
    return {"status": "success", "sync": sync, "result": resolved}


@app.get("/api/utm/release/deviations")
def get_release_deviations(
    release_id: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
) -> Dict[str, Any]:
    rows = list(_get_release_deviations().values())
    if release_id:
        rows = [r for r in rows if str(r.get("release_id", "")) == str(release_id)]
    if status:
        rows = [r for r in rows if str(r.get("status", "")).lower() == str(status).lower()]
    if severity:
        rows = [r for r in rows if str(r.get("severity", "")).lower() == str(severity).lower()]
    rows.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"count": len(rows), "items": rows}}


@app.post("/api/utm/campaigns/start")
def post_campaign_start(payload: InteropCampaignStartPayload) -> Dict[str, Any]:
    # Validate profile exists for campaign context.
    try:
        load_jurisdiction_profile(payload.jurisdiction_profile, PROFILES_DIR)
    except Exception as exc:
        return {"status": "error", "error": "jurisdiction_profile_invalid", "details": str(exc)}
    row = create_campaign(
        campaign_id=payload.campaign_id,
        name=payload.name,
        jurisdiction_profile=payload.jurisdiction_profile,
        release_id=payload.release_id,
        partners=payload.partners,
        scenarios=payload.scenarios,
        scheduled_start=payload.scheduled_start,
        scheduled_end=payload.scheduled_end,
        created_by=payload.created_by,
        notes=payload.notes,
    )
    campaigns = _get_interop_campaigns()
    campaigns[str(row.get("campaign_id"))] = row
    _set_interop_campaigns(campaigns)
    sync = _log_utm_action(
        "interop_campaign_start",
        payload=payload.model_dump(),
        result={"campaign_id": row.get("campaign_id"), "status": row.get("status")},
        entity_id=str(row.get("campaign_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": row}


@app.get("/api/utm/campaigns")
def get_campaigns(release_id: Optional[str] = None) -> Dict[str, Any]:
    rows = list(_get_interop_campaigns().values())
    if release_id:
        rows = [r for r in rows if str(r.get("release_id", "")) == str(release_id)]
    rows.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"count": len(rows), "items": rows}}


@app.get("/api/utm/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> Dict[str, Any]:
    campaigns = _get_interop_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": row}


@app.post("/api/utm/campaigns/{campaign_id}/runs")
def post_campaign_run(campaign_id: str, payload: InteropCampaignRunPayload) -> Dict[str, Any]:
    campaigns = _get_interop_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    updated = append_campaign_run(
        row,
        partner_id=payload.partner_id,
        scenario_id=payload.scenario_id,
        status=payload.status,
        summary=str(payload.summary or ""),
        evidence_ids=payload.evidence_ids,
        metrics=payload.metrics,
    )
    campaigns[campaign_id] = updated
    _set_interop_campaigns(campaigns)
    sync = _log_utm_action(
        "interop_campaign_record_run",
        payload={"campaign_id": campaign_id, **payload.model_dump()},
        result={"campaign_id": campaign_id, "run_count": len(updated.get("run_records") or [])},
        entity_id=campaign_id,
    )
    return {"status": "success", "sync": sync, "result": updated}


@app.post("/api/utm/campaigns/{campaign_id}/report/sign")
def post_campaign_report_sign(campaign_id: str, payload: InteropCampaignSignPayload) -> Dict[str, Any]:
    campaigns = _get_interop_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    signed = sign_campaign_report(
        row,
        signed_by=payload.signed_by,
        signature_ref=payload.signature_ref,
        decision=payload.decision,
        note=str(payload.note or ""),
    )
    campaigns[campaign_id] = signed
    _set_interop_campaigns(campaigns)
    sync = _log_utm_action(
        "interop_campaign_sign_report",
        payload={"campaign_id": campaign_id, **payload.model_dump()},
        result={"campaign_id": campaign_id, "signature_status": (signed.get("report_signature") or {}).get("status")},
        entity_id=campaign_id,
    )
    return {"status": "success", "sync": sync, "result": signed}


@app.get("/api/utm/campaigns/{campaign_id}/report")
def get_campaign_report(campaign_id: str) -> Dict[str, Any]:
    campaigns = _get_interop_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    compliance = _build_compliance_export(limit_actions=150, include_action_payloads=False, include_rtm_text=False)
    report = build_campaign_report(row, compliance_export=compliance)
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": report}


@app.get("/api/utm/security/status")
def get_security_status() -> Dict[str, Any]:
    state = _get_security_controls()
    trust = state.get("trust_store") if isinstance(state.get("trust_store"), dict) else {}
    keys = trust.get("keys") if isinstance(trust.get("keys"), list) else []
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "enforce_service_auth": _enforce_service_auth(),
            "key_count": len(keys),
            "active_keys": len([k for k in keys if isinstance(k, dict) and str(k.get("status", "")).lower() == "active"]),
            "rotation_policy": state.get("key_rotation_policy"),
        },
    }


@app.get("/api/utm/security/trust-store")
def get_security_trust_store() -> Dict[str, Any]:
    state = _get_security_controls()
    trust = state.get("trust_store") if isinstance(state.get("trust_store"), dict) else {}
    keys = trust.get("keys") if isinstance(trust.get("keys"), list) else []
    # Do not expose raw secrets in API responses.
    safe = [
        {
            "issuer": k.get("issuer"),
            "key_id": k.get("key_id"),
            "alg": k.get("alg"),
            "status": k.get("status"),
            "created_at": k.get("created_at"),
            "rotated_at": k.get("rotated_at"),
        }
        for k in keys
        if isinstance(k, dict)
    ]
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"updated_at": trust.get("updated_at"), "keys": safe}}


@app.post("/api/utm/security/trust-store/peers")
def post_security_trust_store_peer(payload: SecurityPeerKeyPayload) -> Dict[str, Any]:
    state = register_peer_key(
        _get_security_controls(),
        issuer=payload.issuer,
        key_id=payload.key_id,
        secret=payload.secret,
        status=payload.status,
    )
    _set_security_controls(state)
    sync = _log_utm_action(
        "security_register_peer_key",
        payload={"issuer": payload.issuer, "key_id": payload.key_id, "status": payload.status},
        result={"issuer": payload.issuer, "key_id": payload.key_id},
        entity_id=str(payload.issuer),
    )
    return {"status": "success", "sync": sync, "result": {"issuer": payload.issuer, "key_id": payload.key_id}}


@app.post("/api/utm/security/keys/rotate")
def post_security_rotate_key(payload: SecurityRotateKeyPayload) -> Dict[str, Any]:
    state, new_key = rotate_signing_key(_get_security_controls(), issuer=payload.issuer)
    _set_security_controls(state)
    sync = _log_utm_action(
        "security_rotate_signing_key",
        payload=payload.model_dump(),
        result={"issuer": payload.issuer, "key_id": new_key.get("key_id")},
        entity_id=str(payload.issuer),
    )
    return {
        "status": "success",
        "sync": sync,
        "result": {
            "issuer": payload.issuer,
            "key_id": new_key.get("key_id"),
            "created_at": new_key.get("created_at"),
        },
    }


@app.post("/api/utm/security/service-tokens")
def post_security_service_token(payload: SecurityTokenPayload) -> Dict[str, Any]:
    state = _get_security_controls()
    tokens = state.get("service_tokens") if isinstance(state.get("service_tokens"), dict) else {}
    tokens[str(payload.token)] = [str(r).strip() for r in payload.roles if str(r).strip()]
    state["service_tokens"] = tokens
    _set_security_controls(state)
    sync = _log_utm_action(
        "security_upsert_service_token",
        payload={"token": f"sha256:{hashlib.sha256(str(payload.token).encode('utf-8')).hexdigest()[:12]}", "roles": payload.roles},
        result={"role_count": len(payload.roles)},
    )
    return {"status": "success", "sync": sync, "result": {"roles": payload.roles}}


@app.get("/api/utm/security/key-rotation-policy")
def get_security_rotation_policy() -> Dict[str, Any]:
    state = _get_security_controls()
    policy = state.get("key_rotation_policy") if isinstance(state.get("key_rotation_policy"), dict) else {}
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": policy}


@app.put("/api/utm/security/key-rotation-policy")
def put_security_rotation_policy(payload: SecurityRotationPolicyPayload) -> Dict[str, Any]:
    state = _get_security_controls()
    state["key_rotation_policy"] = {
        "max_age_days": max(1, int(payload.max_age_days)),
        "overlap_days": max(0, int(payload.overlap_days)),
        "auto_rotate": bool(payload.auto_rotate),
        "last_rotation_at": (
            (state.get("key_rotation_policy") or {}).get("last_rotation_at")
            if isinstance(state.get("key_rotation_policy"), dict)
            else None
        ),
    }
    _set_security_controls(state)
    sync = _log_utm_action(
        "security_update_rotation_policy",
        payload=payload.model_dump(),
        result=state["key_rotation_policy"],
    )
    return {"status": "success", "sync": sync, "result": state["key_rotation_policy"]}


@app.get("/api/utm/operations/playbooks")
def get_operations_playbooks(jurisdiction_profile: str = "us_faa_ntap") -> Dict[str, Any]:
    try:
        profile = load_jurisdiction_profile(jurisdiction_profile, PROFILES_DIR)
    except Exception as exc:
        return {"status": "error", "error": "jurisdiction_profile_invalid", "details": str(exc)}
    incident = dict(profile.get("incident_process") or {}) if isinstance(profile.get("incident_process"), dict) else {}
    required = [str(x).strip() for x in (incident.get("required_artifacts") or []) if str(x).strip()]
    index = build_incident_playbook_index(required_artifacts=required, playbooks_dir=PLAYBOOKS_DIR)
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "profile_id": profile.get("profile_id"),
            "playbooks_dir": str(PLAYBOOKS_DIR),
            "playbook_index": index,
        },
    }


@app.post("/api/utm/operations/readiness/evaluate")
def post_operations_readiness_evaluate(payload: OperationsReadinessPayload) -> Dict[str, Any]:
    try:
        profile = load_jurisdiction_profile(payload.jurisdiction_profile, PROFILES_DIR)
    except Exception as exc:
        return {"status": "error", "error": "jurisdiction_profile_invalid", "details": str(exc)}
    latest_conformance = UTM_DB.get_state("dss_conformance_last")
    readiness = evaluate_operations_readiness(
        profile=profile,
        playbooks_dir=PLAYBOOKS_DIR,
        observed_metrics=dict(payload.observed_metrics or {}),
        latest_conformance=dict(latest_conformance) if isinstance(latest_conformance, dict) else None,
    )
    sync = _log_utm_action(
        "operations_readiness_evaluate",
        payload=payload.model_dump(),
        result={"operations_ready": readiness.get("operations_ready"), "profile_id": readiness.get("profile_id")},
        entity_id=str(readiness.get("profile_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": readiness}


@app.post("/api/utm/resilience/campaigns/start")
def post_resilience_campaign_start(payload: ResilienceCampaignStartPayload) -> Dict[str, Any]:
    row = create_resilience_campaign(
        campaign_id=payload.campaign_id,
        name=payload.name,
        release_id=payload.release_id,
        cadence_days=payload.cadence_days,
        created_by=payload.created_by,
        scenarios=payload.scenarios,
        notes=str(payload.notes or ""),
    )
    campaigns = _get_resilience_campaigns()
    campaigns[str(row.get("campaign_id"))] = row
    _set_resilience_campaigns(campaigns)
    sync = _log_utm_action(
        "resilience_campaign_start",
        payload=payload.model_dump(),
        result={"campaign_id": row.get("campaign_id"), "status": row.get("status")},
        entity_id=str(row.get("campaign_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": row}


@app.get("/api/utm/resilience/campaigns")
def get_resilience_campaigns(release_id: Optional[str] = None) -> Dict[str, Any]:
    rows = list(_get_resilience_campaigns().values())
    if release_id:
        rows = [r for r in rows if str(r.get("release_id", "")) == str(release_id)]
    rows.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": {"count": len(rows), "items": rows}}


@app.get("/api/utm/resilience/campaigns/{campaign_id}")
def get_resilience_campaign(campaign_id: str) -> Dict[str, Any]:
    campaigns = _get_resilience_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": row}


@app.post("/api/utm/resilience/campaigns/{campaign_id}/run")
def post_resilience_campaign_run(campaign_id: str, payload: ResilienceCampaignRunPayload) -> Dict[str, Any]:
    campaigns = _get_resilience_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    scenario_results = (
        [dict(x) for x in payload.scenario_results if isinstance(x, dict)]
        if isinstance(payload.scenario_results, list)
        else build_default_failure_injection_results(payload.fault_profile)
    )
    updated = append_resilience_run(
        row,
        executed_by=payload.executed_by,
        fault_profile=payload.fault_profile,
        scenario_results=scenario_results,
        summary=str(payload.summary or ""),
    )
    campaigns[campaign_id] = updated
    _set_resilience_campaigns(campaigns)
    sync = _log_utm_action(
        "resilience_campaign_run",
        payload={"campaign_id": campaign_id, **payload.model_dump()},
        result={
            "campaign_id": campaign_id,
            "latest_summary": build_resilience_summary(updated),
        },
        entity_id=campaign_id,
    )
    return {"status": "success", "sync": sync, "result": updated}


@app.get("/api/utm/resilience/campaigns/{campaign_id}/summary")
def get_resilience_campaign_summary(campaign_id: str) -> Dict[str, Any]:
    campaigns = _get_resilience_campaigns()
    row = campaigns.get(campaign_id) if isinstance(campaigns.get(campaign_id), dict) else None
    if not isinstance(row, dict):
        return {"status": "error", "error": "campaign_not_found", "campaign_id": campaign_id}
    return {"status": "success", "sync": UTM_DB.get_sync(), "result": build_resilience_summary(row)}


@app.post("/api/utm/checks/route")
def route_checks(payload: RouteCheckPayload) -> Dict[str, Any]:
    rid, waypoints, route_source = _resolve_route_input(payload.uav_id, payload.route_id, payload.waypoints)
    geofence = _geofence_check_from_waypoints(
        uav_id=payload.uav_id,
        route_id=rid,
        airspace_segment=payload.airspace_segment,
        waypoints=waypoints,
    )
    result = {
        "uav_id": payload.uav_id,
        "route_id": rid,
        "route_source": route_source,
        "dataSource": _utm_source_info(),
        "airspace_segment": payload.airspace_segment,
        "waypoints_total": len(waypoints),
        "geofence": geofence,
        "no_fly_zone": UTM_SERVICE.check_no_fly_zones(waypoints),
        "regulations": UTM_SERVICE.check_regulations(waypoints, requested_speed_mps=payload.requested_speed_mps),
        "effectiveRegulations": UTM_SERVICE.effective_regulations(payload.operator_license_id),
    }
    result["regulations"] = UTM_SERVICE.check_regulations(
        waypoints,
        requested_speed_mps=payload.requested_speed_mps,
        operator_license_id=payload.operator_license_id,
    )
    sync = _log_utm_action("route_checks", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {
        "status": "success",
        "sync": sync,
        "result": result,
    }


@app.post("/api/utm/checks/time-window")
def check_time_window(payload: TimeWindowCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_time_window(payload.planned_start_at, payload.planned_end_at, payload.operator_license_id)
    sync = _log_utm_action("check_time_window", payload=payload.model_dump(), result=result)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/checks/license")
def check_license(payload: LicenseCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_operator_license(
        operator_license_id=payload.operator_license_id,
        required_class=payload.required_license_class,
    )
    sync = _log_utm_action("check_license", payload=payload.model_dump(), result=result, entity_id=payload.operator_license_id)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/verify-from-uav")
def verify_from_uav(payload: VerifyFromUavPayload) -> Dict[str, Any]:
    route_id, waypoints, route_source = _resolve_route_input(payload.uav_id, payload.route_id, payload.waypoints)
    result = UTM_SERVICE.verify_flight_plan(
        uav_id=payload.uav_id,
        airspace_segment=payload.airspace_segment,
        route_id=route_id,
        waypoints=waypoints,
        requested_speed_mps=payload.requested_speed_mps,
        planned_start_at=payload.planned_start_at,
        planned_end_at=payload.planned_end_at,
        operator_license_id=payload.operator_license_id,
        required_license_class=payload.required_license_class,
    )
    result["dataSource"] = _utm_source_info()
    result["route_source"] = route_source
    sync = _log_utm_action("verify_from_uav", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}
