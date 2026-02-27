"""UAV + UTM simulator API for frontend controls."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
except Exception as e:  # pragma: no cover
    raise RuntimeError("uav_agent.api requires fastapi and pydantic") from e

from .api_models import (
    ApprovalPayload,
    CorridorPayload,
    FleetCreateUavPayload,
    FleetDeleteUavPayload,
    HoldPayload,
    LicenseCheckPayload,
    LicensePayload,
    NetworkBaseStationUpdatePayload,
    NetworkOptimizePayload,
    NetworkStateQuery,
    NetworkTickPayload,
    NoFlyZonePayload,
    PathRecordDeletePayload,
    PlanRoutePayload,
    ReplanPayload,
    ResetRoutePayload,
    RouteCheckPayload,
    StepPayload,
    TimeWindowCheckPayload,
    UavAgentChatPayload,
    UavLiveTelemetryPayload,
    UavRegistryAssignPayload,
    UavRegistryProfilePayload,
    UavMissionDefaultsPayload,
    UavRegistryUserQueryPayload,
    VerifyFromUavPayload,
    WeatherPayload,
    _dump_waypoint_payload_model,
)
from .copilot_utils import _chat_completion_json, _utm_nfz_conflict_feedback
from .graph import run_copilot_workflow
from .simulator import SIM
from .tools import (
    uav_hold,
    uav_land,
    uav_launch,
    uav_plan_route,
    uav_replan_route_via_utm_nfz,
    uav_request_utm_approval,
    uav_resume,
    uav_return_to_home,
    uav_sim_step,
    uav_status,
    uav_submit_route_to_utm_geofence_check,
)
from agent_db import AgentDB
from network_agent.service import NETWORK_MISSION_SERVICE
from utm_agent.operational_intents import upsert_intent as dss_upsert_intent
from utm_agent.service import UTM_SERVICE

app = FastAPI(title="UAV/UTM Simulator API")
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

UAV_DB = AgentDB("uav")
UTM_DB_MIRROR = AgentDB("utm")
UAV_DATA_MODE = os.getenv("UAV_DATA_MODE", "auto").strip().lower() or "auto"
REAL_UTM_API_BASE = (os.getenv("UAV_REAL_UTM_API_BASE_URL", "http://127.0.0.1:8021") or "").strip().rstrip("/")
REAL_UTM_SYNC_TIMEOUT_S = float((os.getenv("UAV_REAL_UTM_SYNC_TIMEOUT_S", "1.5") or "1.5").strip() or "1.5")
UAV_UTM_LOCAL_ONLY = str(os.getenv("UAV_UTM_LOCAL_ONLY", "1")).strip().lower() in {"1", "true", "yes", "on"}


def _restore_uav_state() -> None:
    fleet = UAV_DB.get_state("fleet")
    if isinstance(fleet, dict):
        SIM.load_fleet_snapshot(fleet)
    utm_state = UAV_DB.get_state("utm_store")
    if isinstance(utm_state, dict):
        UTM_SERVICE.load_state(utm_state)
    net_state = UAV_DB.get_state("network_service")
    if isinstance(net_state, dict):
        NETWORK_MISSION_SERVICE.load_state(net_state)


def _persist_uav_state() -> None:
    UAV_DB.set_state("fleet", SIM.fleet_snapshot())
    UAV_DB.set_state("utm_store", UTM_SERVICE.export_state())
    UAV_DB.set_state("network_service", NETWORK_MISSION_SERVICE.export_state())


def _log_uav_action(action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    _persist_uav_state()
    return UAV_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)


def _log_utm_mirror_action(action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    return UTM_DB_MIRROR.record_action(action, payload=payload, result=result, entity_id=entity_id)


def _refresh_utm_mirror_from_real_service(
    *,
    airspace_segment: str = "sector-A3",
    operator_license_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Refresh the UAV-side UTM mirror from the standalone UTM API, if available.

    UAV endpoints use a local UTM_SERVICE mirror for fast orchestration/session updates. This sync
    keeps that mirror aligned with the "real" UTM service (default :8021) before route checks and
    approval workflows.
    """
    if not REAL_UTM_API_BASE:
        return {"status": "disabled", "reason": "empty_real_utm_api_base"}
    parsed = urlparse(REAL_UTM_API_BASE)
    host = str(parsed.hostname or "").strip().lower()
    is_local_host = host in {"127.0.0.1", "localhost", "::1"}
    if UAV_UTM_LOCAL_ONLY and not is_local_host:
        return {"status": "disabled", "reason": "local_only_mode_non_local_utm_base", "base": REAL_UTM_API_BASE}
    qs: Dict[str, str] = {"airspace_segment": airspace_segment}
    if operator_license_id and str(operator_license_id).strip():
        qs["operator_license_id"] = str(operator_license_id).strip()
    url = f"{REAL_UTM_API_BASE}/api/utm/state?{urlencode(qs)}"
    try:
        with urlopen(url, timeout=max(0.2, REAL_UTM_SYNC_TIMEOUT_S)) as resp:  # nosec B310 - local configurable service endpoint
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"status": "error", "url": url, "error": str(e)}
    if not isinstance(payload, dict):
        return {"status": "error", "url": url, "error": "invalid_response_payload"}
    result = payload.get("result")
    if not isinstance(result, dict):
        return {"status": "error", "url": url, "error": "missing_result"}

    weather = result.get("weather") if isinstance(result.get("weather"), dict) else None
    no_fly_zones = result.get("noFlyZones") if isinstance(result.get("noFlyZones"), list) else None
    regulations = result.get("regulations") if isinstance(result.get("regulations"), dict) else None
    licenses = result.get("licenses") if isinstance(result.get("licenses"), dict) else None
    regulation_profiles = result.get("regulationProfiles") if isinstance(result.get("regulationProfiles"), dict) else None

    if isinstance(weather, dict):
        UTM_SERVICE.set_weather(airspace_segment, **dict(weather))
    if isinstance(no_fly_zones, list):
        UTM_SERVICE.no_fly_zones = [dict(z) for z in no_fly_zones if isinstance(z, dict)]
    if isinstance(regulations, dict):
        UTM_SERVICE.regulations = dict(regulations)
    if isinstance(regulation_profiles, dict):
        UTM_SERVICE.regulation_profiles = {str(k): dict(v) for k, v in regulation_profiles.items() if isinstance(v, dict)}
    if isinstance(licenses, dict):
        UTM_SERVICE.operator_licenses = {str(k): dict(v) for k, v in licenses.items() if isinstance(v, dict)}

    return {
        "status": "success",
        "url": url,
        "airspace_segment": airspace_segment,
        "nfz_count": len(UTM_SERVICE.no_fly_zones),
        "license_count": len(getattr(UTM_SERVICE, "operator_licenses", {}) or {}),
        "source_sync": payload.get("sync") if isinstance(payload.get("sync"), dict) else None,
    }


_restore_uav_state()


def _get_planned_route_history() -> Dict[str, List[Dict[str, Any]]]:
    raw = UAV_DB.get_state("planned_route_history")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for uav_id, rows in raw.items():
        if isinstance(uav_id, str) and isinstance(rows, list):
            out[uav_id] = [dict(r) for r in rows if isinstance(r, dict)]
    return out


def _get_uav_registry() -> Dict[str, Any]:
    raw = UAV_DB.get_state("uav_registry")
    if not isinstance(raw, dict):
        return {"users": {}, "uavs": {}}
    users = raw.get("users") if isinstance(raw.get("users"), dict) else {}
    uavs = raw.get("uavs") if isinstance(raw.get("uavs"), dict) else {}
    return {"users": dict(users), "uavs": dict(uavs)}


def _set_uav_registry(registry: Dict[str, Any]) -> None:
    UAV_DB.set_state("uav_registry", registry)


def _normalize_user_id(user_id: Optional[str]) -> str:
    v = (user_id or "").strip()
    return v or "user-1"


_UAV_REGISTRY_PROFILE_DEFAULTS: Dict[str, Any] = {
    "uav_name": None,
    "uav_serial_number": None,
    "uav_registration_number": None,
    "manufacturer": None,
    "model": None,
    "platform_type": None,
    "uav_category": None,
    "uav_size_class": None,
    "max_takeoff_weight_kg": None,
    "empty_weight_kg": None,
    "payload_capacity_kg": None,
    "max_speed_mps_capability": None,
    "max_altitude_m": None,
    "max_flight_time_min": None,
    "battery_type": None,
    "battery_capacity_mah": None,
    "remote_id_enabled": None,
    "remote_id": None,
    "c2_link_type": None,
    "launch_site_id": None,
    "landing_site_id": None,
    "contingency_action": None,
    "weather_min_visibility_km": None,
    "weather_max_wind_mps": None,
    "home_base_id": None,
    "home_x": None,
    "home_y": None,
    "home_z": None,
    "status": None,
    "firmware_version": None,
    "airworthiness_status": None,
    "last_maintenance_at": None,
    "next_maintenance_due_at": None,
    "owner_org_id": None,
    "owner_name": None,
    "notes": None,
}
_UAV_REGISTRY_PROFILE_STRING_FIELDS = {
    "uav_name",
    "uav_serial_number",
    "uav_registration_number",
    "manufacturer",
    "model",
    "platform_type",
    "uav_category",
    "uav_size_class",
    "battery_type",
    "remote_id",
    "c2_link_type",
    "launch_site_id",
    "landing_site_id",
    "contingency_action",
    "home_base_id",
    "status",
    "firmware_version",
    "airworthiness_status",
    "last_maintenance_at",
    "next_maintenance_due_at",
    "owner_org_id",
    "owner_name",
    "notes",
}
_UAV_REGISTRY_PROFILE_NUMBER_FIELDS = {
    "max_takeoff_weight_kg",
    "empty_weight_kg",
    "payload_capacity_kg",
    "max_speed_mps_capability",
    "max_altitude_m",
    "max_flight_time_min",
    "battery_capacity_mah",
    "weather_min_visibility_km",
    "weather_max_wind_mps",
    "home_x",
    "home_y",
    "home_z",
}
_UAV_REGISTRY_PROFILE_BOOL_FIELDS = {"remote_id_enabled"}

_UAV_MISSION_DEFAULTS_DEFAULTS: Dict[str, Any] = {
    "route_id": None,
    "airspace_segment": "sector-A3",
    "requested_speed_mps": 12.0,
    "planned_start_at": None,
    "planned_end_at": None,
    "hold_reason": "operator_request",
    "mission_priority": None,
    "operation_type": None,
    "c2_link_type": None,
}
_UAV_MISSION_DEFAULTS_STRING_FIELDS = {
    "route_id",
    "airspace_segment",
    "planned_start_at",
    "planned_end_at",
    "hold_reason",
    "mission_priority",
    "operation_type",
    "c2_link_type",
}
_UAV_MISSION_DEFAULTS_NUMBER_FIELDS = {
    "requested_speed_mps",
}


def _sanitize_partial_fields(
    patch: Dict[str, Any],
    *,
    allowed_keys: set[str],
    string_fields: set[str],
    number_fields: set[str],
    bool_fields: set[str] | None = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in patch.items():
        if key not in allowed_keys:
            continue
        if key in string_fields:
            if value is None:
                out[key] = None
            else:
                s = str(value).strip()
                out[key] = s or None
            continue
        if key in number_fields:
            if value is None or value == "":
                out[key] = None
            else:
                try:
                    out[key] = float(value)
                except Exception:
                    continue
            continue
        if bool_fields and key in bool_fields:
            out[key] = None if value is None else bool(value)
            continue
        out[key] = value
    return out


def _get_uav_registry_profile(uav_id: str) -> Dict[str, Any]:
    row = _get_uav_registry_uav_row(uav_id)
    raw = row.get("standardized_profile") if isinstance(row.get("standardized_profile"), dict) else {}
    merged = dict(_UAV_REGISTRY_PROFILE_DEFAULTS)
    if isinstance(raw, dict):
        merged.update({k: raw.get(k) for k in _UAV_REGISTRY_PROFILE_DEFAULTS.keys()})
    return merged


def _uav_capability_issues_for_request(
    *,
    uav_id: str,
    requested_speed_mps: Optional[float] = None,
    waypoints: Optional[list[dict]] = None,
) -> list[str]:
    profile = _get_uav_registry_profile(uav_id)
    issues: list[str] = []
    cap_speed = profile.get("max_speed_mps_capability")
    if requested_speed_mps is not None and isinstance(cap_speed, (int, float)):
        try:
            req_speed = float(requested_speed_mps)
            cap_speed_f = float(cap_speed)
            if math.isfinite(req_speed) and math.isfinite(cap_speed_f) and cap_speed_f > 0 and req_speed > cap_speed_f:
                issues.append(f"requested_speed_mps {req_speed:.2f} exceeds UAV capability max_speed_mps_capability {cap_speed_f:.2f}")
        except Exception:
            pass
    cap_alt = profile.get("max_altitude_m")
    if isinstance(cap_alt, (int, float)):
        try:
            cap_alt_f = float(cap_alt)
            if math.isfinite(cap_alt_f) and cap_alt_f > 0:
                route = waypoints if isinstance(waypoints, list) else _sim_waypoints(uav_id)[1]
                route_max_z: float | None = None
                for w in route:
                    if not isinstance(w, dict):
                        continue
                    try:
                        z = float(w.get("z", 0.0))
                    except Exception:
                        continue
                    if not math.isfinite(z):
                        continue
                    route_max_z = z if route_max_z is None else max(route_max_z, z)
                if route_max_z is not None and route_max_z > cap_alt_f:
                    issues.append(f"route max altitude {route_max_z:.2f}m exceeds UAV capability max_altitude_m {cap_alt_f:.2f}m")
        except Exception:
            pass
    return issues


def _enforce_uav_capability_limits_or_raise(
    *,
    uav_id: str,
    requested_speed_mps: Optional[float] = None,
    waypoints: Optional[list[dict]] = None,
    context: str = "uav_capability_check",
) -> None:
    issues = _uav_capability_issues_for_request(
        uav_id=uav_id,
        requested_speed_mps=requested_speed_mps,
        waypoints=waypoints,
    )
    if not issues:
        return
    raise HTTPException(
        status_code=400,
        detail={
            "error": "uav_capability_limit_violation",
            "context": context,
            "uav_id": uav_id,
            "issues": issues,
            "uav_registry_profile": _get_uav_registry_profile(uav_id),
        },
    )


def _save_uav_registry_profile(*, user_id: Optional[str], uav_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    registry = _get_uav_registry()
    users = registry.get("users") if isinstance(registry.get("users"), dict) else {}
    uavs = registry.get("uavs") if isinstance(registry.get("uavs"), dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    urow = uavs.get(uav_id) if isinstance(uavs.get(uav_id), dict) else {}
    if not isinstance(urow, dict):
        urow = {}
    if user_id and str(user_id).strip():
        urow["owner_user_id"] = _normalize_user_id(user_id)
    elif not str(urow.get("owner_user_id", "")).strip():
        urow["owner_user_id"] = "user-1"
    if not str(urow.get("operator_license_id", "")).strip():
        urow["operator_license_id"] = "op-001"
    current = urow.get("standardized_profile") if isinstance(urow.get("standardized_profile"), dict) else {}
    profile = dict(_UAV_REGISTRY_PROFILE_DEFAULTS)
    if isinstance(current, dict):
        profile.update({k: current.get(k) for k in _UAV_REGISTRY_PROFILE_DEFAULTS.keys()})
    profile.update(
        _sanitize_partial_fields(
            patch,
            allowed_keys=set(_UAV_REGISTRY_PROFILE_DEFAULTS.keys()),
            string_fields=_UAV_REGISTRY_PROFILE_STRING_FIELDS,
            number_fields=_UAV_REGISTRY_PROFILE_NUMBER_FIELDS,
            bool_fields=_UAV_REGISTRY_PROFILE_BOOL_FIELDS,
        )
    )
    urow["standardized_profile"] = profile
    urow["updated_at"] = now_iso
    uavs[uav_id] = urow
    _set_uav_registry({"users": users, "uavs": uavs})
    return _get_uav_registry_profile(uav_id)


def _get_uav_mission_defaults_store() -> Dict[str, Dict[str, Any]]:
    raw = UAV_DB.get_state("uav_mission_defaults")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, row in raw.items():
        if isinstance(key, str) and isinstance(row, dict):
            out[key] = dict(row)
    return out


def _set_uav_mission_defaults_store(store: Dict[str, Dict[str, Any]]) -> None:
    UAV_DB.set_state("uav_mission_defaults", store)


def _uav_mission_defaults_key(*, user_id: str, uav_id: str) -> str:
    return f"{_normalize_user_id(user_id)}:{uav_id}"


def _get_uav_mission_defaults(*, user_id: str, uav_id: str) -> Dict[str, Any]:
    store = _get_uav_mission_defaults_store()
    key = _uav_mission_defaults_key(user_id=user_id, uav_id=uav_id)
    row = store.get(key) if isinstance(store.get(key), dict) else {}
    if not isinstance(row, dict):
        row = {}
    merged = {
        "key": key,
        "user_id": _normalize_user_id(user_id),
        "uav_id": uav_id,
        **dict(_UAV_MISSION_DEFAULTS_DEFAULTS),
    }
    for k in _UAV_MISSION_DEFAULTS_DEFAULTS.keys():
        if k in row:
            merged[k] = row.get(k)
    merged["updated_at"] = row.get("updated_at")
    return merged


def _save_uav_mission_defaults(*, user_id: str, uav_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_uav_mission_defaults_store()
    key = _uav_mission_defaults_key(user_id=user_id, uav_id=uav_id)
    prev = store.get(key) if isinstance(store.get(key), dict) else {}
    row = dict(prev) if isinstance(prev, dict) else {}
    row.update(
        _sanitize_partial_fields(
            patch,
            allowed_keys=set(_UAV_MISSION_DEFAULTS_DEFAULTS.keys()),
            string_fields=_UAV_MISSION_DEFAULTS_STRING_FIELDS,
            number_fields=_UAV_MISSION_DEFAULTS_NUMBER_FIELDS,
        )
    )
    row["user_id"] = _normalize_user_id(user_id)
    row["uav_id"] = uav_id
    row["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store[key] = row
    _set_uav_mission_defaults_store(store)
    return _get_uav_mission_defaults(user_id=_normalize_user_id(user_id), uav_id=uav_id)


def _get_uav_registry_uav_row(uav_id: str) -> Dict[str, Any]:
    registry = _get_uav_registry()
    uavs = registry.get("uavs") if isinstance(registry.get("uavs"), dict) else {}
    row = uavs.get(uav_id)
    return dict(row) if isinstance(row, dict) else {}


def _resolve_session_user_id(*, uav_id: str, user_id: Optional[str]) -> str:
    if user_id and str(user_id).strip():
        return _normalize_user_id(user_id)
    row = _get_uav_registry_uav_row(uav_id)
    owner = row.get("owner_user_id") if isinstance(row, dict) else None
    return _normalize_user_id(str(owner) if owner else None)


def _get_uav_utm_sessions() -> Dict[str, Dict[str, Any]]:
    raw = UAV_DB.get_state("uav_utm_sessions")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _set_uav_utm_sessions(sessions: Dict[str, Dict[str, Any]]) -> None:
    UAV_DB.set_state("uav_utm_sessions", sessions)


def _uav_utm_session_key(*, user_id: str, uav_id: str) -> str:
    return f"{_normalize_user_id(user_id)}:{uav_id}"


def _get_uav_utm_session(*, user_id: str, uav_id: str) -> Dict[str, Any]:
    sessions = _get_uav_utm_sessions()
    key = _uav_utm_session_key(user_id=user_id, uav_id=uav_id)
    row = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
    if not isinstance(row, dict):
        row = {}
    return {
        "key": key,
        "user_id": _normalize_user_id(user_id),
        "uav_id": uav_id,
        "utm_approval": dict(row.get("utm_approval")) if isinstance(row.get("utm_approval"), dict) else None,
        "utm_geofence_result": dict(row.get("utm_geofence_result")) if isinstance(row.get("utm_geofence_result"), dict) else None,
        "utm_dss_result": dict(row.get("utm_dss_result")) if isinstance(row.get("utm_dss_result"), dict) else None,
        "updated_at": row.get("updated_at"),
    }


def _save_uav_utm_session(
    *,
    user_id: str,
    uav_id: str,
    utm_approval: Optional[Dict[str, Any]] = None,
    utm_geofence_result: Optional[Dict[str, Any]] = None,
    utm_dss_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    sessions = _get_uav_utm_sessions()
    key = _uav_utm_session_key(user_id=user_id, uav_id=uav_id)
    prev = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
    row = dict(prev) if isinstance(prev, dict) else {}
    row["user_id"] = _normalize_user_id(user_id)
    row["uav_id"] = uav_id
    if utm_approval is not None:
        row["utm_approval"] = dict(utm_approval)
    if utm_geofence_result is not None:
        row["utm_geofence_result"] = dict(utm_geofence_result)
    if utm_dss_result is not None:
        row["utm_dss_result"] = dict(utm_dss_result)
    row["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sessions[key] = row
    _set_uav_utm_sessions(sessions)
    return _get_uav_utm_session(user_id=_normalize_user_id(user_id), uav_id=uav_id)


def _sync_sim_utm_from_session(*, user_id: str, uav_id: str) -> Dict[str, Any]:
    session = _get_uav_utm_session(user_id=user_id, uav_id=uav_id)
    if isinstance(session.get("utm_geofence_result"), dict):
        SIM.set_geofence_result(uav_id, dict(session["utm_geofence_result"]))
    if isinstance(session.get("utm_approval"), dict):
        SIM.set_approval(uav_id, dict(session["utm_approval"]))
    return session


def _get_local_dss_operational_intents() -> Dict[str, Dict[str, Any]]:
    raw = UTM_DB_MIRROR.get_state("dss_operational_intents")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _set_local_dss_operational_intents(values: Dict[str, Dict[str, Any]]) -> None:
    UTM_DB_MIRROR.set_state("dss_operational_intents", values)


def _route_volume4d_for_waypoints(
    waypoints: list[dict],
    *,
    planned_start_at: str | None = None,
    planned_end_at: str | None = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = planned_start_at or now.isoformat().replace("+00:00", "Z")
    end = planned_end_at or (now + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    if not waypoints:
        return {"x": [-1e9, 1e9], "y": [-1e9, 1e9], "z": [0.0, 120.0], "time_start": start, "time_end": end}
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


def _upsert_local_dss_intent_for_uav(
    *,
    user_id: str,
    uav_id: str,
    route_id: str,
    waypoints: list[dict],
    airspace_segment: str,
    conflict_policy: str = "reject",
    planned_start_at: str | None = None,
    planned_end_at: str | None = None,
) -> Dict[str, Any]:
    intents = _get_local_dss_operational_intents()
    intent_id = f"{_normalize_user_id(user_id)}:{uav_id}:{route_id}"
    volume4d = _route_volume4d_for_waypoints(
        waypoints,
        planned_start_at=planned_start_at,
        planned_end_at=planned_end_at,
    )
    out = dss_upsert_intent(
        intents,
        intent_id=intent_id,
        manager_uss_id=f"uss-local-{_normalize_user_id(user_id)}",
        state="accepted",
        priority="normal",
        conflict_policy=conflict_policy,
        volume4d=volume4d,
        metadata={
            "source": "uav_request_approval",
            "user_id": _normalize_user_id(user_id),
            "uav_id": uav_id,
            "route_id": route_id,
            "airspace_segment": airspace_segment,
        },
    )
    if out.get("stored"):
        _set_local_dss_operational_intents(intents)
    return out


def _ensure_registry_seed() -> None:
    registry = _get_uav_registry()
    users = registry.get("users") if isinstance(registry.get("users"), dict) else {}
    uavs = registry.get("uavs") if isinstance(registry.get("uavs"), dict) else {}
    changed = False
    if not users and not uavs:
        users["user-1"] = {"uav_ids": [], "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
        changed = True
    fleet = SIM.fleet_snapshot()
    for uav_id in fleet.keys():
        if not isinstance(uavs.get(uav_id), dict):
            uavs[uav_id] = {
                "owner_user_id": "user-1",
                "operator_license_id": "op-001",
                "standardized_profile": dict(_UAV_REGISTRY_PROFILE_DEFAULTS),
                "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            changed = True
        elif not isinstance((uavs[uav_id] or {}).get("standardized_profile"), dict):
            uavs[uav_id]["standardized_profile"] = dict(_UAV_REGISTRY_PROFILE_DEFAULTS)  # type: ignore[index]
            changed = True
        owner = str((uavs[uav_id] or {}).get("owner_user_id", "user-1"))
        user_row = users.setdefault(owner, {"uav_ids": [], "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
        if isinstance(user_row, dict):
            ids = user_row.get("uav_ids") if isinstance(user_row.get("uav_ids"), list) else []
            if uav_id not in ids:
                ids.append(uav_id)
                user_row["uav_ids"] = ids
                user_row["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                changed = True
    if changed:
        _set_uav_registry({"users": users, "uavs": uavs})


def _assign_uav_to_user(*, user_id: str, uav_id: str, operator_license_id: Optional[str] = None) -> Dict[str, Any]:
    registry = _get_uav_registry()
    users = registry.get("users") if isinstance(registry.get("users"), dict) else {}
    uavs = registry.get("uavs") if isinstance(registry.get("uavs"), dict) else {}
    user_id = _normalize_user_id(user_id)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for uid, row in list(users.items()):
        if not isinstance(row, dict):
            continue
        ids = [str(x) for x in row.get("uav_ids", [])] if isinstance(row.get("uav_ids"), list) else []
        if uav_id in ids and uid != user_id:
            row["uav_ids"] = [x for x in ids if x != uav_id]
            row["updated_at"] = now_iso
    user_row = users.setdefault(user_id, {"uav_ids": [], "updated_at": now_iso})
    if isinstance(user_row, dict):
        ids = [str(x) for x in user_row.get("uav_ids", [])] if isinstance(user_row.get("uav_ids"), list) else []
        if uav_id not in ids:
            ids.append(uav_id)
        user_row["uav_ids"] = ids
        user_row["updated_at"] = now_iso
    urow = uavs.get(uav_id) if isinstance(uavs.get(uav_id), dict) else {}
    if not isinstance(urow, dict):
        urow = {}
    urow["owner_user_id"] = user_id
    if operator_license_id is not None and str(operator_license_id).strip():
        urow["operator_license_id"] = str(operator_license_id).strip()
    elif "operator_license_id" not in urow:
        urow["operator_license_id"] = "op-001"
    if not isinstance(urow.get("standardized_profile"), dict):
        urow["standardized_profile"] = dict(_UAV_REGISTRY_PROFILE_DEFAULTS)
    urow["updated_at"] = now_iso
    uavs[uav_id] = urow
    out = {"users": users, "uavs": uavs}
    _set_uav_registry(out)
    return out


def _remove_uav_from_registry(uav_id: str) -> None:
    registry = _get_uav_registry()
    users = registry.get("users") if isinstance(registry.get("users"), dict) else {}
    uavs = registry.get("uavs") if isinstance(registry.get("uavs"), dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for row in users.values():
        if not isinstance(row, dict):
            continue
        ids = [str(x) for x in row.get("uav_ids", [])] if isinstance(row.get("uav_ids"), list) else []
        if uav_id in ids:
            row["uav_ids"] = [x for x in ids if x != uav_id]
            row["updated_at"] = now_iso
    if uav_id in uavs:
        del uavs[uav_id]
    _set_uav_registry({"users": users, "uavs": uavs})


def _registry_user_summary(user_id: str) -> Dict[str, Any]:
    _ensure_registry_seed()
    registry = _get_uav_registry()
    users = registry.get("users") if isinstance(registry.get("users"), dict) else {}
    uavs = registry.get("uavs") if isinstance(registry.get("uavs"), dict) else {}
    user_id = _normalize_user_id(user_id)
    row = users.get(user_id) if isinstance(users.get(user_id), dict) else {"uav_ids": []}
    uav_ids = [str(x) for x in row.get("uav_ids", [])] if isinstance(row.get("uav_ids"), list) else []
    utm_licenses = UTM_SERVICE.operator_licenses if isinstance(UTM_SERVICE.operator_licenses, dict) else {}
    uav_rows: list[dict] = []
    for uid in uav_ids:
        meta = uavs.get(uid) if isinstance(uavs.get(uid), dict) else {}
        lic_id = str(meta.get("operator_license_id", "op-001")) if isinstance(meta, dict) else "op-001"
        snap = SIM.fleet_snapshot().get(uid, {})
        profile = _get_uav_registry_profile(uid)
        mission_defaults = _get_uav_mission_defaults(user_id=user_id, uav_id=uid)
        uav_rows.append(
            {
                "uav_id": uid,
                "operator_license_id": lic_id,
                "operator_license": utm_licenses.get(lic_id),
                "registry_profile": profile,
                "mission_defaults": mission_defaults,
                "sim_status": snap,
            }
        )
    return {
        "user_id": user_id,
        "uav_count": len(uav_rows),
        "uavs": uav_rows,
        "registry": registry,
        "utm_licenses": utm_licenses,
    }


def _set_planned_route_history(history: Dict[str, List[Dict[str, Any]]]) -> None:
    UAV_DB.set_state("planned_route_history", history)


def _record_planned_route_history(*, uav_id: str, route_id: str, waypoints: list[dict], source: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    history = _get_planned_route_history()
    rows = history.get(uav_id, [])
    meta = dict(metadata) if isinstance(metadata, dict) else {}
    source_l = str(source or "").strip().lower()
    category = str(meta.get("route_category", "") or "").strip()
    if not category:
        if source_l in {"plan", "create_uav", "reset_route"}:
            category = "user_planned"
        elif source_l in {"agent_copilot", "replan_via_utm_nfz"}:
            category = "agent_replanned"
        else:
            category = "other"
    if category == "agent_replanned":
        # Keep agent replans explicitly tied to the latest user-planned route without mutating user_planned.
        associated_user_row = None
        for row in reversed(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            m = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if str(m.get("route_category", "")) == "user_planned":
                associated_user_row = row
                break
        if isinstance(associated_user_row, dict):
            if not str(meta.get("associated_user_planned_route_id", "")).strip():
                meta["associated_user_planned_route_id"] = str(associated_user_row.get("route_id", "") or "")
            if not str(meta.get("associated_user_planned_created_at", "")).strip():
                meta["associated_user_planned_created_at"] = associated_user_row.get("created_at")
            if not str(meta.get("association_type", "")).strip():
                meta["association_type"] = "derived_from_user_planned"
    # Keep only the latest path per category for a UAV so mission overlays remain stable and uncluttered.
    rows = [
        r for r in rows
        if not (
            isinstance(r, dict)
            and str((r.get("metadata") or {}).get("route_category", "") if isinstance(r.get("metadata"), dict) else "") == category
        )
    ]
    rows.append(
        {
            "uav_id": uav_id,
            "route_id": route_id,
            "waypoints": [dict(w) for w in waypoints],
            "source": source,
            "metadata": {**meta, "route_category": category},
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "active": True,
        }
    )
    for i in range(max(0, len(rows) - 1)):
        rows[i]["active"] = False
    history[uav_id] = rows[-20:]
    _set_planned_route_history(history)


def _get_approved_flight_path_history(db: AgentDB, state_key: str) -> Dict[str, List[Dict[str, Any]]]:
    raw = db.get_state(state_key)
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for k, rows in raw.items():
        if isinstance(k, str) and isinstance(rows, list):
            out[k] = [dict(r) for r in rows if isinstance(r, dict)]
    return out


def _set_approved_flight_path_history(db: AgentDB, state_key: str, history: Dict[str, List[Dict[str, Any]]]) -> None:
    db.set_state(state_key, history)


def _record_approved_flight_path_history(
    *,
    db: AgentDB,
    state_key: str,
    user_id: str,
    uav_id: str,
    route_id: str,
    waypoints: list[dict],
    approval: Dict[str, Any],
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    history = _get_approved_flight_path_history(db, state_key)
    key = f"{user_id}:{uav_id}"
    rows = history.get(key, [])
    row = {
        "user_id": user_id,
        "uav_id": uav_id,
        "mission_id": metadata.get("mission_id") if isinstance(metadata, dict) else None,
        "route_id": route_id,
        "waypoints": [dict(w) for w in waypoints],
        "approval_id": approval.get("approval_id"),
        "approved": bool(approval.get("approved")),
        "signature_verified": bool(approval.get("signature_verified")),
        "airspace_segment": approval.get("airspace_segment"),
        "operator_license_id": approval.get("operator_license_id"),
        "source": source,
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    rows.append(row)
    # Keep only the latest UTM-confirmed route per user+uav mission scope.
    history[key] = [row]
    _set_approved_flight_path_history(db, state_key, history)
    return row


def _get_mission_records(db: AgentDB, state_key: str = "mission_records") -> Dict[str, Any]:
    raw = db.get_state(state_key)
    if not isinstance(raw, dict):
        return {"by_scope": {}, "by_id": {}}
    by_scope = raw.get("by_scope") if isinstance(raw.get("by_scope"), dict) else {}
    by_id = raw.get("by_id") if isinstance(raw.get("by_id"), dict) else {}
    return {"by_scope": dict(by_scope), "by_id": dict(by_id)}


def _set_mission_records(db: AgentDB, records: Dict[str, Any], state_key: str = "mission_records") -> None:
    db.set_state(state_key, records)


def _persist_mission_record_to_both(record: Dict[str, Any]) -> None:
    for db in (UAV_DB, UTM_DB_MIRROR):
        recs = _get_mission_records(db)
        by_scope = recs.get("by_scope") if isinstance(recs.get("by_scope"), dict) else {}
        by_id = recs.get("by_id") if isinstance(recs.get("by_id"), dict) else {}
        scope_key = f"{record.get('user_id')}:{record.get('uav_id')}"
        by_scope[scope_key] = str(record.get("mission_id"))
        by_id[str(record.get("mission_id"))] = dict(record)
        _set_mission_records(db, {"by_scope": by_scope, "by_id": by_id})


def _current_mission_record(*, user_id: str, uav_id: str) -> Dict[str, Any] | None:
    recs = _get_mission_records(UAV_DB)
    by_scope = recs.get("by_scope") if isinstance(recs.get("by_scope"), dict) else {}
    by_id = recs.get("by_id") if isinstance(recs.get("by_id"), dict) else {}
    mid = by_scope.get(f"{user_id}:{uav_id}")
    row = by_id.get(mid) if isinstance(mid, str) and isinstance(by_id.get(mid), dict) else None
    return dict(row) if isinstance(row, dict) else None


def _mission_id_for(*, user_id: str, uav_id: str, route_id: str, planned_start_at: Optional[str]) -> str:
    planned_tag = (planned_start_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    planned_tag = planned_tag.replace(":", "").replace("-", "").replace(".", "").replace("+", "").replace("/", "_")
    return f"mission-{user_id}-{uav_id}-{route_id}-{planned_tag}"


def _ensure_agent_path_copy_if_missing(*, uav_id: str, route_id: str, waypoints: list[dict], mission_id: str, user_id: str, source_hint: str) -> None:
    mission_paths = _latest_mission_paths_for(user_id=user_id, uav_id=uav_id)
    agent_row = mission_paths.get("agent_replanned") if isinstance(mission_paths, dict) else None
    if isinstance(agent_row, dict):
        return
    _record_planned_route_history(
        uav_id=uav_id,
        route_id=route_id,
        waypoints=waypoints,
        source="replan_via_utm_nfz",
        metadata={"route_category": "agent_replanned", "mission_id": mission_id, "copied_from": source_hint, "auto_copied": True},
    )


def _stamp_mission_id_on_route_category(*, uav_id: str, category: str, mission_id: str) -> None:
    hist = _get_planned_route_history().get(uav_id, [])
    target = None
    if isinstance(hist, list):
        for row in reversed(hist):
            if not isinstance(row, dict):
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if str(meta.get("route_category", "")) == category:
                target = row
                break
    if not isinstance(target, dict):
        return
    meta = target.get("metadata") if isinstance(target.get("metadata"), dict) else {}
    if str(meta.get("mission_id", "")) == mission_id:
        return
    waypoints = [dict(w) for w in target.get("waypoints", [])] if isinstance(target.get("waypoints"), list) else []
    _record_planned_route_history(
        uav_id=uav_id,
        route_id=str(target.get("route_id", "route-1")),
        waypoints=waypoints,
        source=str(target.get("source", "plan")),
        metadata={**meta, "route_category": category, "mission_id": mission_id},
    )


def _save_verified_mission_and_paths(
    *,
    user_id: str,
    uav_id: str,
    route_id: str,
    waypoints: list[dict],
    approval: Dict[str, Any],
    source: str,
    planned_start_at: Optional[str] = None,
    planned_end_at: Optional[str] = None,
    copy_agent_if_missing: bool = False,
) -> Dict[str, Any] | None:
    if not isinstance(approval, dict) or not bool(approval.get("approved")) or not bool(approval.get("signature_verified")):
        return None
    current = _current_mission_record(user_id=user_id, uav_id=uav_id)
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    effective_planned_start = planned_start_at or (
        (approval.get("scope") or {}).get("time_window", [None, None])[0] if isinstance(approval.get("scope"), dict) else None
    )
    effective_planned_end = planned_end_at or (
        (approval.get("scope") or {}).get("time_window", [None, None])[1] if isinstance(approval.get("scope"), dict) else None
    )
    if current and str(current.get("route_id")) == route_id and bool(current.get("approved")):
        mission = dict(current)
    else:
        mission = {
            "mission_id": _mission_id_for(user_id=user_id, uav_id=uav_id, route_id=route_id, planned_start_at=str(effective_planned_start or "")),
            "user_id": user_id,
            "uav_id": uav_id,
            "route_id": route_id,
            "created_at": now_iso,
            "execute_started_at": None,
            "execute_last_action_at": None,
            "execute_completed_at": None,
        }
    mission.update(
        {
            "approved": True,
            "approval_id": approval.get("approval_id"),
            "operator_license_id": approval.get("operator_license_id"),
            "airspace_segment": approval.get("airspace_segment"),
            "planned_start_at": effective_planned_start,
            "planned_end_at": effective_planned_end,
            "approved_at": now_iso,
            "approval_source": source,
            "paths": {
                "user_planned_route_id": None,
                "agent_replanned_route_id": None,
                "utm_confirmed_route_id": route_id,
            },
        }
    )
    # Do not mutate user_planned automatically during approval/verification flows.
    # user_planned must only change through explicit user planning actions.
    if copy_agent_if_missing:
        _ensure_agent_path_copy_if_missing(
            uav_id=uav_id,
            route_id=route_id,
            waypoints=waypoints,
            mission_id=str(mission["mission_id"]),
            user_id=user_id,
            source_hint=source,
        )
    _stamp_mission_id_on_route_category(uav_id=uav_id, category="agent_replanned", mission_id=str(mission["mission_id"]))
    # Refresh path route ids after copy/dedupe.
    latest_paths = _latest_mission_paths_for(user_id=user_id, uav_id=uav_id)
    if isinstance(latest_paths, dict):
        user_row = latest_paths.get("user_planned") if isinstance(latest_paths.get("user_planned"), dict) else {}
        agent_row = latest_paths.get("agent_replanned") if isinstance(latest_paths.get("agent_replanned"), dict) else {}
        mission["paths"] = {
            "user_planned_route_id": str(user_row.get("route_id", "") or "") or None,
            "agent_replanned_route_id": str(agent_row.get("route_id", "") or "") or None,
            "utm_confirmed_route_id": route_id,
        }
    common_meta = {"mission_id": mission["mission_id"], "approval_source": source}
    uav_row = _record_approved_flight_path_history(
        db=UAV_DB,
        state_key="approved_flight_path_history",
        user_id=user_id,
        uav_id=uav_id,
        route_id=route_id,
        waypoints=waypoints,
        approval=approval,
        source=source,
        metadata=common_meta,
    )
    utm_row = _record_approved_flight_path_history(
        db=UTM_DB_MIRROR,
        state_key="approved_flight_path_history",
        user_id=user_id,
        uav_id=uav_id,
        route_id=route_id,
        waypoints=waypoints,
        approval=approval,
        source=source,
        metadata=common_meta,
    )
    _persist_mission_record_to_both(mission)
    _log_utm_mirror_action(
        "utm_approved_flight_path_saved",
        payload={
            "user_id": user_id,
            "uav_id": uav_id,
            "route_id": route_id,
            "airspace_segment": approval.get("airspace_segment"),
            "operator_license_id": approval.get("operator_license_id"),
            "source": source,
            "mission_id": mission.get("mission_id"),
        },
        result={"approval_id": approval.get("approval_id"), "approved": True, "mission_id": mission.get("mission_id")},
        entity_id=uav_id,
    )
    return {"mission": mission, "approved_route_records": {"uav_db": uav_row, "utm_db": utm_row}}


def _touch_mission_execution(*, user_id: str, uav_id: str, action: str) -> None:
    mission = _current_mission_record(user_id=user_id, uav_id=uav_id)
    if not mission:
        return
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    mission["execute_last_action_at"] = now_iso
    if action == "launch" and not mission.get("execute_started_at"):
        mission["execute_started_at"] = now_iso
    if action == "land":
        mission["execute_completed_at"] = now_iso
    _persist_mission_record_to_both(mission)


def _latest_mission_paths_for(*, user_id: str, uav_id: str) -> Dict[str, Any]:
    planned = _get_planned_route_history().get(uav_id, [])
    user_row: Dict[str, Any] | None = None
    agent_row: Dict[str, Any] | None = None
    if isinstance(planned, list):
        for row in reversed(planned):
            if not isinstance(row, dict):
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            cat = str(meta.get("route_category", ""))
            if cat == "user_planned" and user_row is None:
                user_row = dict(row)
            elif cat == "agent_replanned" and agent_row is None:
                agent_row = dict(row)
            if user_row is not None and agent_row is not None:
                break
    approved_hist = _get_approved_flight_path_history(UAV_DB, "approved_flight_path_history")
    approved_rows = approved_hist.get(f"{user_id}:{uav_id}", [])
    utm_row = None
    if isinstance(approved_rows, list):
        for row in reversed(approved_rows):
            if isinstance(row, dict):
                utm_row = dict(row)
                break
    return {
        "user_planned": user_row,
        "agent_replanned": agent_row,
        "utm_confirmed": utm_row,
    }


def _route_metrics_from_waypoints(waypoints: list[dict], *, default_speed_mps: float = 12.0) -> Dict[str, Any]:
    pts = [dict(w) for w in waypoints if isinstance(w, dict)]
    origin_counts = {"original": 0, "agent_inserted": 0, "other": 0}
    mapped_replacement_count = 0
    mapped_from_original_indices: list[int] = []
    for p in pts:
        origin = str(p.get("_wp_origin", "original") or "original")
        if origin in origin_counts:
            origin_counts[origin] += 1
        else:
            origin_counts["other"] += 1
        mapped_idx = p.get("_mapped_from_original_index")
        if isinstance(mapped_idx, int):
            mapped_replacement_count += 1
            mapped_from_original_indices.append(mapped_idx)
    if not pts:
        return {
            "waypoints_total": 0,
            "start": None,
            "end": None,
            "distance_m": 0.0,
            "estimated_flight_seconds": None,
            "estimated_flight_minutes": None,
            "estimated_speed_mps": default_speed_mps,
            "waypoint_origin_counts": origin_counts,
            "mapped_replacement_count": mapped_replacement_count,
            "mapped_from_original_indices": mapped_from_original_indices,
        }
    distance_m = 0.0
    for i in range(1, len(pts)):
        a = pts[i - 1]
        b = pts[i]
        ax, ay, az = float(a.get("x", 0.0)), float(a.get("y", 0.0)), float(a.get("z", 0.0))
        bx, by, bz = float(b.get("x", 0.0)), float(b.get("y", 0.0)), float(b.get("z", 0.0))
        distance_m += math.dist((ax, ay, az), (bx, by, bz))
    speed_mps = max(0.1, float(default_speed_mps or 12.0))
    est_seconds = distance_m / speed_mps if len(pts) >= 2 else None
    return {
        "waypoints_total": len(pts),
        "start": {k: pts[0].get(k) for k in ("x", "y", "z", "action")},
        "end": {k: pts[-1].get(k) for k in ("x", "y", "z", "action")},
        "distance_m": round(distance_m, 2),
        "estimated_flight_seconds": round(est_seconds, 1) if isinstance(est_seconds, float) else None,
        "estimated_flight_minutes": round((est_seconds or 0.0) / 60.0, 2) if isinstance(est_seconds, float) else None,
        "estimated_speed_mps": round(speed_mps, 2),
        "waypoint_origin_counts": origin_counts,
        "mapped_replacement_count": mapped_replacement_count,
        "mapped_from_original_indices": sorted(set(mapped_from_original_indices)),
    }


def _path_record_row_summary(
    *,
    category: str,
    label: str,
    color: str,
    user_id: str,
    uav_id: str,
    row: Dict[str, Any] | None,
    in_uav_db: bool,
    in_utm_db: bool,
) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {
            "category": category,
            "label": label,
            "color": color,
            "exists": False,
            "user_id": user_id,
            "uav_id": uav_id,
            "db_presence": {"uav_db": in_uav_db, "utm_db": in_utm_db},
        }
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    waypoints = [dict(w) for w in row.get("waypoints", [])] if isinstance(row.get("waypoints"), list) else []
    speed_guess = metadata.get("requested_speed_mps") if isinstance(metadata, dict) else None
    metrics = _route_metrics_from_waypoints(
        waypoints,
        default_speed_mps=float(speed_guess) if isinstance(speed_guess, (int, float)) else 12.0,
    )
    return {
        "category": category,
        "label": label,
        "color": color,
        "exists": True,
        "source": row.get("source"),
        "route_id": row.get("route_id"),
        "mission_id": row.get("mission_id") or (metadata.get("mission_id") if isinstance(metadata, dict) else None),
        "approval_id": row.get("approval_id"),
        "created_at": row.get("created_at"),
        "user_id": row.get("user_id") or (metadata.get("user_id") if isinstance(metadata, dict) else user_id),
        "uav_id": row.get("uav_id") or uav_id,
        "approved": row.get("approved"),
        "signature_verified": row.get("signature_verified"),
        "operator_license_id": row.get("operator_license_id"),
        "airspace_segment": row.get("airspace_segment"),
        "db_presence": {"uav_db": in_uav_db, "utm_db": in_utm_db},
        "associations": {
            "associated_user_planned_route_id": metadata.get("associated_user_planned_route_id") if isinstance(metadata, dict) else None,
            "associated_user_planned_created_at": metadata.get("associated_user_planned_created_at") if isinstance(metadata, dict) else None,
            "association_type": metadata.get("association_type") if isinstance(metadata, dict) else None,
        },
        "metrics": metrics,
        "replan_stats": {
            "replan_round_index": metadata.get("replan_round_index") if isinstance(metadata, dict) else None,
            "inserted_waypoints_count": metadata.get("inserted_waypoints_count") if isinstance(metadata, dict) else None,
            "waypoint_deletions_count": metadata.get("waypoint_deletions_count") if isinstance(metadata, dict) else None,
            "los_prune_deletions_count": metadata.get("los_prune_deletions_count") if isinstance(metadata, dict) else None,
            "los_prune_passes_count": metadata.get("los_prune_passes_count") if isinstance(metadata, dict) else None,
            "inserted_trim_deletions_count": metadata.get("inserted_trim_deletions_count") if isinstance(metadata, dict) else None,
            "inserted_trim_passes_count": metadata.get("inserted_trim_passes_count") if isinstance(metadata, dict) else None,
            "replaced_original_waypoints_count": metadata.get("replaced_original_waypoints_count") if isinstance(metadata, dict) else None,
        },
    }


def _path_records_summary_for(*, user_id: str, uav_id: str) -> Dict[str, Any]:
    mission_paths = _latest_mission_paths_for(user_id=user_id, uav_id=uav_id)
    utm_hist = _get_approved_flight_path_history(UTM_DB_MIRROR, "approved_flight_path_history")
    utm_rows = utm_hist.get(f"{user_id}:{uav_id}", [])
    utm_latest = None
    if isinstance(utm_rows, list):
        for row in reversed(utm_rows):
            if isinstance(row, dict):
                utm_latest = dict(row)
                break
    user_row = mission_paths.get("user_planned") if isinstance(mission_paths.get("user_planned"), dict) else None
    agent_row = mission_paths.get("agent_replanned") if isinstance(mission_paths.get("agent_replanned"), dict) else None
    utm_row = mission_paths.get("utm_confirmed") if isinstance(mission_paths.get("utm_confirmed"), dict) else None
    uav_utm_route_id = str((utm_row or {}).get("route_id") or "")
    utm_mirror_route_id = str((utm_latest or {}).get("route_id") or "")
    utm_in_both = bool(utm_row) and bool(utm_latest) and (not uav_utm_route_id or uav_utm_route_id == utm_mirror_route_id)
    rows = {
        "user_planned": _path_record_row_summary(
            category="user_planned",
            label="User Planned",
            color="#2563eb",
            user_id=user_id,
            uav_id=uav_id,
            row=user_row,
            in_uav_db=bool(user_row),
            in_utm_db=False,
        ),
        "agent_replanned": _path_record_row_summary(
            category="agent_replanned",
            label="Agent Replanned",
            color="#f79009",
            user_id=user_id,
            uav_id=uav_id,
            row=agent_row,
            in_uav_db=bool(agent_row),
            in_utm_db=False,
        ),
        "utm_confirmed": _path_record_row_summary(
            category="utm_confirmed",
            label="UTM Approved",
            color="#12b76a",
            user_id=user_id,
            uav_id=uav_id,
            row=utm_row,
            in_uav_db=bool(utm_row),
            in_utm_db=utm_in_both or bool(utm_latest),
        ),
    }
    if isinstance(utm_latest, dict):
        rows["utm_confirmed"]["utm_db_route_id"] = utm_latest.get("route_id")
        rows["utm_confirmed"]["utm_db_created_at"] = utm_latest.get("created_at")
        rows["utm_confirmed"]["utm_db_approval_id"] = utm_latest.get("approval_id")
    return {
        "scope": {"user_id": user_id, "uav_id": uav_id},
        "rows": rows,
        "order": ["user_planned", "agent_replanned", "utm_confirmed"],
        "sync": {"uav_db": UAV_DB.get_sync(), "utm_db": UTM_DB_MIRROR.get_sync()},
    }


def _latest_planned_routes_summary() -> Dict[str, Dict[str, Any]]:
    history = _get_planned_route_history()
    out: Dict[str, Dict[str, Any]] = {}
    for uav_id, rows in history.items():
        if not rows:
            continue
        last = rows[-1]
        if isinstance(last, dict):
            out[uav_id] = dict(last)
    return out


def _delete_planned_route_history(uav_id: str) -> None:
    history = _get_planned_route_history()
    if uav_id in history:
        del history[uav_id]
        _set_planned_route_history(history)


def _delete_planned_route_history_category(*, uav_id: str, category: str) -> bool:
    history = _get_planned_route_history()
    rows = history.get(uav_id)
    if not isinstance(rows, list):
        return False
    next_rows = [
        r for r in rows
        if not (
            isinstance(r, dict)
            and str((r.get("metadata") or {}).get("route_category", "") if isinstance(r.get("metadata"), dict) else "") == category
        )
    ]
    if len(next_rows) == len(rows):
        return False
    for i, row in enumerate(next_rows):
        if isinstance(row, dict):
            row["active"] = i == len(next_rows) - 1
    if next_rows:
        history[uav_id] = next_rows[-20:]
    else:
        history.pop(uav_id, None)
    _set_planned_route_history(history)
    return True


def _delete_approved_flight_path_for_scope(*, db: AgentDB, state_key: str, user_id: str, uav_id: str) -> bool:
    history = _get_approved_flight_path_history(db, state_key)
    key = f"{user_id}:{uav_id}"
    if key not in history:
        return False
    del history[key]
    _set_approved_flight_path_history(db, state_key, history)
    return True


def _generate_reset_route_from_position(pos: Dict[str, Any]) -> List[Dict[str, float]]:
    x0 = float(pos.get("x", 0.0))
    y0 = float(pos.get("y", 0.0))
    z0 = max(0.0, float(pos.get("z", 0.0)))
    cruise = max(30.0, z0 if z0 > 0 else 40.0)
    pts = [
        {"x": x0, "y": y0, "z": z0},
        {"x": min(400.0, x0 + 55.0), "y": min(300.0, y0 + 25.0), "z": min(120.0, cruise)},
        {"x": min(400.0, x0 + 115.0), "y": max(0.0, y0 - 15.0), "z": min(120.0, cruise + 10.0)},
        {"x": min(400.0, x0 + 165.0), "y": min(300.0, y0 + 45.0), "z": min(120.0, cruise)},
    ]
    return pts


_ensure_registry_seed()


def _uav_data_mode() -> str:
    mode = UAV_DATA_MODE
    return mode if mode in {"sim", "real", "auto"} else "auto"


def _uav_data_source_info(uav_id: str) -> Dict[str, Any]:
    snap = SIM.status_if_exists(uav_id) or {}
    return {
        "mode": _uav_data_mode(),
        "active": str(snap.get("data_source", "absent") or "absent"),
        "meta": snap.get("data_source_meta") if isinstance(snap.get("data_source_meta"), dict) else None,
        "lastUpdateTs": snap.get("last_update_ts"),
    }


def _default_time_window() -> tuple[str, str]:
    now = datetime.now(timezone.utc) + timedelta(minutes=2)
    end = now + timedelta(minutes=20)
    return (
        now.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def _flight_control_gate_issues(uav_id: str, *, action: str, user_id: Optional[str] = None) -> list[str]:
    snap = SIM.status_if_exists(uav_id)
    if not isinstance(snap, dict):
        return ["UAV not found"]
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    session = _get_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id)
    if isinstance(session.get("utm_approval"), dict):
        snap["utm_approval"] = dict(session["utm_approval"])
    if isinstance(session.get("utm_geofence_result"), dict):
        snap["utm_geofence_result"] = dict(session["utm_geofence_result"])
    if isinstance(session.get("utm_dss_result"), dict):
        snap["utm_dss_result"] = dict(session["utm_dss_result"])
    issues: list[str] = []
    battery = snap.get("battery_pct")
    if isinstance(battery, (int, float)) and float(battery) < 15.0:
        issues.append(f"Battery low ({float(battery):.0f}%)")
    wps = snap.get("waypoints")
    if not isinstance(wps, list) or len(wps) < 2:
        issues.append("Planned path requires at least 2 waypoints")

    geofence = snap.get("utm_geofence_result")
    if isinstance(geofence, dict):
        if geofence.get("ok") is not True and geofence.get("geofence_ok") is not True:
            issues.append("Geofence / NFZ check not passed")
    else:
        issues.append("Geofence / NFZ check not available")

    approval = snap.get("utm_approval")
    if isinstance(approval, dict):
        if approval.get("approved") is not True:
            issues.append("UTM approval not granted")
        if approval.get("signature_verified") is False:
            issues.append("UTM approval signature not verified")
        checks = approval.get("checks") if isinstance(approval.get("checks"), dict) else {}
        if isinstance(checks, dict):
            weather = checks.get("weather")
            if isinstance(weather, dict) and weather.get("ok") is False:
                issues.append("UTM weather check failed")
            for label, key in [
                ("NFZ", "no_fly_zone"),
                ("Regulations", "regulations"),
                ("Time window", "time_window"),
                ("Operator license", "operator_license"),
            ]:
                chk = checks.get(key)
                if isinstance(chk, dict) and chk.get("ok") is False:
                    issues.append(f"{label} check failed")
    else:
        issues.append("UTM approval not available")
    dss_result = snap.get("utm_dss_result")
    if isinstance(dss_result, dict):
        blocking = dss_result.get("blocking_conflicts")
        conflict_summary = dss_result.get("intent", {}).get("conflict_summary") if isinstance(dss_result.get("intent"), dict) else {}
        blocking_count = 0
        if isinstance(blocking, list):
            blocking_count = len(blocking)
        elif isinstance(conflict_summary, dict):
            blocking_count = int(conflict_summary.get("blocking", 0) or 0)
        if blocking_count > 0:
            issues.append(f"DSS strategic conflict unresolved ({blocking_count} blocking)")
    action_l = str(action or "").strip().lower()
    if action_l in {"step", "hold", "resume", "rth", "land"}:
        armed = bool(snap.get("armed"))
        active = bool(snap.get("active"))
        if not armed:
            issues.insert(0, f"Please launch before {action_l}. UAV is not launched.")
            return issues
        if action_l == "step" and not active:
            issues.insert(0, "Cannot step while UAV is not active. Please resume mission before step.")
        if action_l == "resume" and active:
            issues.insert(0, "Cannot resume because UAV is already active.")
        if action_l == "hold" and not active:
            issues.insert(0, "Cannot hold because UAV is not active.")
        if action_l == "rth" and str(snap.get("flight_phase", "")).upper() == "RTH":
            issues.insert(0, "Cannot return-to-home because UAV is already in RTH.")
        if action_l == "land" and str(snap.get("flight_phase", "")).upper() == "LAND":
            issues.insert(0, "Cannot land because UAV is already landed.")
    return issues


def _enforce_flight_control_gate_or_raise(*, uav_id: str, action: str, user_id: Optional[str] = None) -> None:
    issues = _flight_control_gate_issues(uav_id, action=action, user_id=user_id)
    if not issues:
        return
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    detail = {
        "status": "error",
        "error": "flight_control_blocked",
        "action": action,
        "uav_id": uav_id,
        "user_id": resolved_user_id,
        "issues": issues,
    }
    sync = _log_uav_action(f"{action}_blocked", payload={"uav_id": uav_id, "user_id": resolved_user_id}, result=detail, entity_id=uav_id)
    detail["sync"] = sync
    raise HTTPException(status_code=409, detail=detail)


def _sim_waypoints(uav_id: str) -> tuple[str, list[dict]]:
    sim = SIM.status(uav_id)
    route_id = str(sim.get("route_id", "route-1"))
    waypoints = list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else []
    return route_id, waypoints


def _geofence_check_from_waypoints(*, uav_id: str, route_id: str, airspace_segment: str, waypoints: list[dict]) -> Dict[str, Any]:
    bounds = {"sector-A3": {"x": [0, 400], "y": [0, 300], "z": [0, 120]}}
    seg = bounds.get(airspace_segment, {"x": [-1e9, 1e9], "y": [-1e9, 1e9], "z": [0, 120]})
    out_of_bounds = []
    for i, wp in enumerate(waypoints):
        x = float(wp.get("x", 0.0))
        y = float(wp.get("y", 0.0))
        z = float(wp.get("z", 0.0))
        if not (seg["x"][0] <= x <= seg["x"][1] and seg["y"][0] <= y <= seg["y"][1] and seg["z"][0] <= z <= seg["z"][1]):
            out_of_bounds.append({"index": i, "wp": {"x": x, "y": y, "z": z}})
    nfz = UTM_SERVICE.check_no_fly_zones(waypoints)
    bounds_ok = len(out_of_bounds) == 0
    return {
        "uav_id": uav_id,
        "route_id": route_id,
        "airspace_segment": airspace_segment,
        # Geofence is route-bounds only. NFZ is reported separately in `no_fly_zone`.
        "ok": bounds_ok,
        "geofence_ok": bounds_ok,
        "bounds_ok": bounds_ok,
        "out_of_bounds": out_of_bounds,
        "no_fly_zone": nfz,
    }


def _build_copilot_context(payload: UavAgentChatPayload, *, route_id: str, effective_waypoints: list[dict]) -> Dict[str, Any]:
    sim_state = SIM.status(payload.uav_id)
    network_state_full = NETWORK_MISSION_SERVICE.get_state(
        airspace_segment=payload.airspace_segment,
        selected_uav_id=payload.uav_id,
    )
    network_state = network_state_full.get("result") if isinstance(network_state_full, dict) else None
    return {
        "mission": {
            "uav_id": payload.uav_id,
            "airspace_segment": payload.airspace_segment,
            "prompt": payload.prompt,
            "route_id": route_id,
            "optimization_profile": payload.optimization_profile,
            "auto_verify": payload.auto_verify,
            "auto_network_optimize": payload.auto_network_optimize,
            "requested_network_mode": payload.network_mode,
        },
        "waypoints": effective_waypoints,
        "utm": {
            "weather": UTM_SERVICE.get_weather(payload.airspace_segment),
            "no_fly_zones": list(UTM_SERVICE.no_fly_zones),
            "regulations": dict(UTM_SERVICE.regulations),
        },
        "network": network_state,
        "uav": sim_state,
    }


def _normalize_copilot_actions(plan: Dict[str, Any], payload: UavAgentChatPayload) -> list[Dict[str, Any]]:
    raw_actions = plan.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = []
    out: list[Dict[str, Any]] = []
    for rec in raw_actions[:5]:
        if not isinstance(rec, dict):
            continue
        tool = str(rec.get("tool", rec.get("action", "")) or "").strip().lower()
        args = rec.get("arguments", rec.get("args", {}))
        if not isinstance(args, dict):
            args = {}
        if tool in {"replan", "replan_route", "route_replan"}:
            out.append({"tool": "replan_route", "args": args})
        elif tool in {"verify", "verify_flight_plan", "utm_verify"}:
            out.append({"tool": "verify_flight_plan", "args": args})
        elif tool in {"network_optimize", "optimize_network"}:
            out.append({"tool": "network_optimize", "args": args})
        elif tool in {"hold", "uav_hold"}:
            out.append({"tool": "hold", "args": args})
        elif tool in {"noop", "none", "respond_only"}:
            out.append({"tool": "noop", "args": args})
    has_verify = any(a["tool"] == "verify_flight_plan" for a in out)
    has_net = any(a["tool"] == "network_optimize" for a in out)
    if payload.auto_verify and not has_verify:
        out.append({"tool": "verify_flight_plan", "args": {"reason": "auto_verify_policy"}})
    if payload.auto_network_optimize and not has_net:
        out.append({"tool": "network_optimize", "args": {"reason": "auto_network_optimize_policy"}})
    return out or [{"tool": "noop", "args": {}}]


def _llm_plan_actions(payload: UavAgentChatPayload, context: Dict[str, Any]) -> Dict[str, Any]:
    system_prompt = (
        "You are a UAV copilot planner. "
        "Decide a short sequence of actions for a UAV mission assistant. "
        "Available tools: replan_route, verify_flight_plan, network_optimize, hold, noop. "
        "Use at most 4 actions. Prefer safe behavior. "
        "Return ONLY JSON with keys: assistant_response (string), actions (array). "
        "Each action item must be an object with tool and arguments. "
        "Use network_optimize.mode in {coverage,qos,power} if chosen. "
        "If no tool is needed, return noop."
    )
    user_payload = {
        "task": "plan_uav_copilot_actions",
        "context": context,
        "hints": {
            "route_replan_tool": "uav_replan_route_via_utm_nfz",
            "verify_tool": "UTM_SERVICE.verify_flight_plan",
            "network_tool": "NETWORK_MISSION_SERVICE.apply_optimization",
            "hold_tool": "uav_hold",
        },
    }
    resp = _chat_completion_json(system_prompt=system_prompt, user_payload=user_payload)
    if resp.get("status") != "success":
        return resp
    parsed = resp.get("parsed") if isinstance(resp.get("parsed"), dict) else {}
    return {
        "status": "success",
        "model": resp.get("model"),
        "raw": resp.get("raw"),
        "assistant_response": str(parsed.get("assistant_response", "") or "").strip(),
        "actions": _normalize_copilot_actions(parsed, payload),
    }


def _summarize_tool_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out: Dict[str, Any] = {"status": result.get("status", "unknown")}
    r = result.get("result")
    if isinstance(r, dict):
        for key in ("route_id", "uav_id", "approved", "ok", "mode"):
            if key in r:
                out[key] = r.get(key)
    for key in ("approved", "mode", "tool"):
        if key in result and key not in out:
            out[key] = result.get(key)
    if "error" in result:
        out["error"] = result.get("error")
    return out


def _execute_copilot_actions(
    payload: UavAgentChatPayload,
    *,
    prompt: str,
    route_id: str,
    effective_waypoints: list[dict],
    actions: list[Dict[str, Any]],
) -> Dict[str, Any]:
    messages: List[str] = []
    tool_trace: List[Dict[str, Any]] = []
    replan_result: Dict[str, Any] | None = None
    verify_result: Dict[str, Any] | None = None
    net_opt_result: Dict[str, Any] | None = None
    chosen_network_mode: str | None = None

    for idx, action in enumerate(actions, start=1):
        tool = str(action.get("tool", "") or "")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        if tool == "noop":
            tool_trace.append({"step": idx, "tool": "noop", "status": "skipped"})
            continue

        if tool == "replan_route":
            replan_args = {
                "uav_id": payload.uav_id,
                "airspace_segment": payload.airspace_segment,
                "user_request": str(args.get("user_request", prompt) or prompt),
                "route_id": str(args.get("route_id", route_id) or route_id),
                "waypoints": effective_waypoints,
                "optimization_profile": str(args.get("optimization_profile", payload.optimization_profile) or payload.optimization_profile),
            }
            replan_result = uav_replan_route_via_utm_nfz.invoke(replan_args)
            step_status = str(replan_result.get("status", "unknown")) if isinstance(replan_result, dict) else "unknown"
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "uav_replan_route_via_utm_nfz",
                    "status": step_status,
                    "args": {"optimization_profile": replan_args["optimization_profile"]},
                    "summary": _summarize_tool_result(replan_result),
                }
            )
            if step_status == "success":
                rr = replan_result.get("result", {}) if isinstance(replan_result, dict) else {}
                changes = rr.get("changes") if isinstance(rr, dict) else None
                messages.append(f"Replanned route ({replan_args['optimization_profile']}); changes={len(changes) if isinstance(changes, list) else 0}.")
                sim_now = SIM.status(payload.uav_id)
                effective_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else effective_waypoints
            else:
                messages.append("Route replan failed.")
            continue

        if tool == "verify_flight_plan":
            sim_now = SIM.status(payload.uav_id)
            current_route_id = str(sim_now.get("route_id", route_id))
            current_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else []
            verify_result = UTM_SERVICE.verify_flight_plan(
                uav_id=payload.uav_id,
                airspace_segment=payload.airspace_segment,
                route_id=current_route_id,
                waypoints=current_waypoints,
                operator_license_id=str(args.get("operator_license_id", payload.operator_license_id) or payload.operator_license_id),
                required_license_class=str(args.get("required_license_class", "VLOS") or "VLOS"),
                requested_speed_mps=float(args.get("requested_speed_mps", sim_now.get("velocity_mps", 12.0) or 12.0)),
                planned_start_at=str(args.get("planned_start_at")) if args.get("planned_start_at") else None,
                planned_end_at=str(args.get("planned_end_at")) if args.get("planned_end_at") else None,
            )
            approved = bool(verify_result.get("approved")) if isinstance(verify_result, dict) else False
            messages.append(f"UTM verification: {'approved' if approved else 'not approved'}.")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "utm_verify_flight_plan",
                    "status": "success",
                    "approved": approved,
                    "summary": _summarize_tool_result({"status": "success", "approved": approved, "result": verify_result}),
                }
            )
            continue

        if tool == "network_optimize":
            mode = str(args.get("mode", payload.network_mode or "") or "").lower().strip()
            if mode not in {"coverage", "qos", "power"}:
                if any(k in prompt.lower() for k in ["qos", "latency", "loss", "video"]):
                    mode = "qos"
                elif "power" in prompt.lower():
                    mode = "power"
                else:
                    mode = "coverage"
            chosen_network_mode = mode
            net_opt_result = NETWORK_MISSION_SERVICE.apply_optimization(mode=mode)
            ok = isinstance(net_opt_result, dict) and net_opt_result.get("status") == "success"
            messages.append(f"{'Applied' if ok else 'Failed'} network optimization mode: {mode}.")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "network_apply_optimization",
                    "status": "success" if ok else "error",
                    "mode": mode,
                    "summary": _summarize_tool_result(net_opt_result),
                }
            )
            continue

        if tool == "hold":
            reason = str(args.get("reason", "copilot_safety_hold") or "copilot_safety_hold")
            hold_result = uav_hold.invoke({"uav_id": payload.uav_id, "reason": reason})
            ok = isinstance(hold_result, dict) and hold_result.get("status") == "success"
            messages.append("Placed UAV in hold." if ok else "Failed to place UAV in hold.")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "uav_hold",
                    "status": "success" if ok else "error",
                    "reason": reason,
                    "summary": _summarize_tool_result(hold_result),
                }
            )
            continue

        tool_trace.append({"step": idx, "tool": tool, "status": "skipped", "reason": "unsupported_action"})

    return {
        "messages": messages,
        "toolTrace": tool_trace,
        "replan": replan_result,
        "utmVerify": verify_result,
        "networkOptimization": net_opt_result,
        "networkMode": chosen_network_mode,
    }


def _llm_summarize_outcome(
    *,
    payload: UavAgentChatPayload,
    context_before: Dict[str, Any],
    tool_trace: list[Dict[str, Any]],
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    system_prompt = (
        "You are a UAV copilot assistant. Summarize what happened after tool execution. "
        "Return ONLY JSON with keys: response (string), messages (array of short strings). "
        "Do not invent tool results."
    )
    user_payload = {
        "task": "summarize_uav_copilot_outcome",
        "prompt": payload.prompt,
        "context_before": context_before,
        "tool_trace": tool_trace,
        "execution": execution,
    }
    return _chat_completion_json(system_prompt=system_prompt, user_payload=user_payload)


def _run_uav_agent_chat_heuristic(payload: UavAgentChatPayload) -> Dict[str, Any]:
    prompt = (payload.prompt or "").strip()
    sim_before = SIM.status(payload.uav_id)
    route_id = payload.route_id or str(sim_before.get("route_id", "route-1"))
    input_waypoints = [_dump_waypoint_payload_model(w) for w in payload.waypoints] if payload.waypoints else None
    effective_waypoints = input_waypoints if input_waypoints else (list(sim_before.get("waypoints", [])) if isinstance(sim_before.get("waypoints"), list) else [])
    if len(effective_waypoints) >= 2:
        SIM.plan_route(payload.uav_id, route_id=route_id, waypoints=effective_waypoints)
    else:
        result = {"status": "error", "error": "route_requires_at_least_two_waypoints"}
        sync = _log_uav_action("agent_chat", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
        return {"status": "error", "sync": sync, "result": result}

    p = prompt.lower()
    wants_route_opt = any(k in p for k in ["replan", "path", "route", "optimiz"])
    wants_network = payload.auto_network_optimize or any(k in p for k in ["coverage", "signal", "network", "sinr", "qos", "latency", "power"])
    net_mode = str(payload.network_mode or "").lower().strip()
    if net_mode not in {"coverage", "qos", "power"}:
        net_mode = "coverage"
        if any(k in p for k in ["qos", "latency", "loss", "video"]):
            net_mode = "qos"
        elif "power" in p:
            net_mode = "power"

    messages: List[str] = []
    tool_trace: List[Dict[str, Any]] = []
    replan_result: Dict[str, Any] | None = None
    if wants_route_opt:
        replan_result = uav_replan_route_via_utm_nfz.invoke(
            {
                "uav_id": payload.uav_id,
                "airspace_segment": payload.airspace_segment,
                "user_request": prompt,
                "route_id": route_id,
                "waypoints": effective_waypoints,
                "optimization_profile": payload.optimization_profile,
            }
        )
        if isinstance(replan_result, dict) and replan_result.get("status") == "success":
            rr = replan_result.get("result")
            if isinstance(rr, dict):
                changes = rr.get("changes")
                n_changes = len(changes) if isinstance(changes, list) else 0
                deletions = rr.get("waypoint_deletions")
                n_deleted = len(deletions) if isinstance(deletions, list) else 0
                messages.append(
                    f"Replanned route ({payload.optimization_profile}) to avoid UTM no-fly zones "
                    f"({n_changes} changes, {n_deleted} waypoint removals)."
                )
            tool_trace.append({"tool": "uav_replan_route_via_utm_nfz", "status": "success", "profile": payload.optimization_profile})
        else:
            messages.append("Route replan failed.")
            tool_trace.append({"tool": "uav_replan_route_via_utm_nfz", "status": "error"})
    else:
        messages.append("Kept current route (no replan requested).")
        tool_trace.append({"tool": "route_replan", "status": "skipped"})

    verify_result: Dict[str, Any] | None = None
    if payload.auto_verify:
        sim_now = SIM.status(payload.uav_id)
        current_route_id = str(sim_now.get("route_id", route_id))
        current_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else []
        verify_result = UTM_SERVICE.verify_flight_plan(
            uav_id=payload.uav_id,
            airspace_segment=payload.airspace_segment,
            route_id=current_route_id,
            waypoints=current_waypoints,
            operator_license_id=payload.operator_license_id,
            required_license_class="VLOS",
            requested_speed_mps=float(sim_now.get("velocity_mps", 12.0) or 12.0),
        )
        messages.append(f"UTM verification: {'approved' if verify_result.get('approved') else 'not approved'}.")
        conflict_fb = _utm_nfz_conflict_feedback(verify_result)
        decision_obj = verify_result.get("decision") if isinstance(verify_result.get("decision"), dict) else None
        if isinstance(decision_obj, dict):
            for m in decision_obj.get("messages", []) if isinstance(decision_obj.get("messages"), list) else []:
                messages.append(f"UTM: {str(m)}")
            for s in decision_obj.get("suggestions", []) if isinstance(decision_obj.get("suggestions"), list) else []:
                messages.append(f"Suggestion: {str(s)}")
        tool_trace.append(
            {
                "tool": "utm_verify_flight_plan",
                "status": "success",
                "approved": bool(verify_result.get("approved")),
                "utm_decision": decision_obj,
                "nfz_conflict_feedback": conflict_fb if conflict_fb["has_conflict"] else None,
            }
        )
        if conflict_fb["has_conflict"] and not bool(verify_result.get("approved")):
            conflict_hint = conflict_fb["summary"] or "NFZ conflict detected"
            messages.append(f"UTM detected no-fly-zone conflict at {conflict_hint}. Requesting UAV route regeneration.")
            corrective_prompt = (
                f"Avoid no-fly zones and fix conflicts at {conflict_hint}. "
                f"Regenerate route around restricted zones while keeping mission path valid."
            )
            corrective_replan = uav_replan_route_via_utm_nfz.invoke(
                {
                    "uav_id": payload.uav_id,
                    "airspace_segment": payload.airspace_segment,
                    "user_request": corrective_prompt,
                    "route_id": current_route_id,
                    "waypoints": current_waypoints,
                    "optimization_profile": payload.optimization_profile,
                }
            )
            replanned_ok = isinstance(corrective_replan, dict) and corrective_replan.get("status") == "success"
            tool_trace.append(
                {
                    "tool": "uav_replan_route_via_utm_nfz",
                    "status": "success" if replanned_ok else "error",
                    "reason": "auto_repair_after_utm_verify_nfz_conflict",
                    "conflicts": conflict_fb,
                }
            )
            if replanned_ok:
                sim_after_replan = SIM.status(payload.uav_id)
                current_route_id = str(sim_after_replan.get("route_id", current_route_id))
                current_waypoints = list(sim_after_replan.get("waypoints", [])) if isinstance(sim_after_replan.get("waypoints"), list) else current_waypoints
                verify_result = UTM_SERVICE.verify_flight_plan(
                    uav_id=payload.uav_id,
                    airspace_segment=payload.airspace_segment,
                    route_id=current_route_id,
                    waypoints=current_waypoints,
                    operator_license_id=payload.operator_license_id,
                    required_license_class="VLOS",
                    requested_speed_mps=float(sim_after_replan.get("velocity_mps", 12.0) or 12.0),
                )
                approved2 = bool(verify_result.get("approved")) if isinstance(verify_result, dict) else False
                messages.append(f"UTM re-verification after route regeneration: {'approved' if approved2 else 'not approved'}.")
                decision_obj2 = verify_result.get("decision") if isinstance(verify_result.get("decision"), dict) else None
                if isinstance(decision_obj2, dict):
                    for m in decision_obj2.get("messages", []) if isinstance(decision_obj2.get("messages"), list) else []:
                        messages.append(f"UTM: {str(m)}")
                tool_trace.append(
                    {
                        "tool": "utm_verify_flight_plan",
                        "status": "success",
                        "approved": approved2,
                        "reason": "post_auto_repair_reverify",
                        "utm_decision": decision_obj2,
                        "nfz_conflict_feedback": _utm_nfz_conflict_feedback(verify_result),
                    }
                )
            else:
                messages.append("Automatic route regeneration failed after UTM conflict feedback.")

    net_opt_result: Dict[str, Any] | None = None
    if wants_network:
        net_opt_result = NETWORK_MISSION_SERVICE.apply_optimization(mode=net_mode)
        if isinstance(net_opt_result, dict) and net_opt_result.get("status") == "success":
            messages.append(f"Applied network optimization mode: {net_mode}.")
            tool_trace.append({"tool": "network_apply_optimization", "status": "success", "mode": net_mode})
        else:
            messages.append("Network optimization failed.")
            tool_trace.append({"tool": "network_apply_optimization", "status": "error", "mode": net_mode})

    network_state = NETWORK_MISSION_SERVICE.get_state(airspace_segment=payload.airspace_segment, selected_uav_id=payload.uav_id)
    sim_after = SIM.status(payload.uav_id)
    agent_result = {
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "prompt": prompt,
        "optimizationProfile": payload.optimization_profile,
        "networkMode": net_mode if wants_network else None,
        "autoVerify": payload.auto_verify,
        "autoNetworkOptimize": wants_network,
        "messages": messages,
        "toolTrace": tool_trace,
        "uav": sim_after,
        "replan": replan_result,
        "utmVerify": verify_result,
        "networkOptimization": net_opt_result,
        "networkState": network_state.get("result") if isinstance(network_state, dict) else None,
        "copilot": {
            "mode": "heuristic",
            "llm": {
                "enabled": False,
                "reason": "Ollama planner unavailable (langchain_ollama missing or Ollama not reachable)",
            },
        },
    }
    sync = _log_uav_action("agent_chat", payload=payload.model_dump(), result=agent_result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": agent_result}




# Export all shared names (including underscore helpers) for internal route modules.
__all__ = [name for name in globals() if not name.startswith("__")]
