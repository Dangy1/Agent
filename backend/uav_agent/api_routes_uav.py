"""UAV-domain API routes (simulator, registry, copilot, mission actions)."""

from __future__ import annotations

import math
import os

from fastapi import APIRouter, HTTPException

from .api_shared import *  # noqa: F401,F403
from .api_models import (
    MissionActionPayload,
    UavControlBridgeCommandPayload,
    UavControlBridgeResponsePayload,
    UavDemoSeedPayload,
)
from .command_adapter import parse_control_operation
from .simulator import (
    LEGACY_LOCAL_MAX_X_M,
    LEGACY_LOCAL_MAX_Y_M,
    MAP_RELEVANCE_RADIUS_M,
    OTANIEMI_CENTER_LAT,
    OTANIEMI_CENTER_LON,
)
from geo_utils import haversine_m, lon_lat_from_local_xy_m

router = APIRouter()

_MISSION_ACTION_ALIASES: dict[str, str] = {
    "photo": "photo",
    "take_photo": "photo",
    "capture_photo": "photo",
    "measure": "temperature",
    "temperature": "temperature",
    "inspect": "inspect",
    "hover": "hover",
}

_CONTROL_BRIDGE_STUB_STATE_KEY = "uav_control_bridge_stub_state"
_CONTROL_BRIDGE_STUB_ADAPTER = {
    "name": "mavlink_bridge_stub",
    "protocol": "mavlink",
    "bridge_mode": "stub",
    "api_version": "v1",
}


def _coerce_add_uav_lon_lat(lon: float, lat: float) -> tuple[float, float]:
    x = float(lon)
    y = float(lat)
    looks_local_xy = 0.0 <= x <= LEGACY_LOCAL_MAX_X_M and 0.0 <= y <= LEGACY_LOCAL_MAX_Y_M
    if -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0:
        if haversine_m(x, y, OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT) <= MAP_RELEVANCE_RADIUS_M:
            return x, y
        if looks_local_xy:
            return lon_lat_from_local_xy_m(x, y, ref_lon=OTANIEMI_CENTER_LON, ref_lat=OTANIEMI_CENTER_LAT)
        return OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT
    if looks_local_xy:
        return lon_lat_from_local_xy_m(x, y, ref_lon=OTANIEMI_CENTER_LON, ref_lat=OTANIEMI_CENTER_LAT)
    return OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT


def _control_stub_state_all() -> Dict[str, Dict[str, Any]]:
    raw = UAV_DB.get_state(_CONTROL_BRIDGE_STUB_STATE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _control_stub_save_all(state_map: Dict[str, Dict[str, Any]]) -> None:
    UAV_DB.set_state(_CONTROL_BRIDGE_STUB_STATE_KEY, state_map)


def _control_stub_default_state(uav_id: str) -> Dict[str, Any]:
    snap = SIM.status_if_exists(uav_id) or {}
    pos_raw = snap.get("position") if isinstance(snap.get("position"), dict) else {}
    pos = point_with_aliases(pos_raw)
    return {
        "uav_id": uav_id,
        "route_id": str(snap.get("route_id", "route-1") or "route-1"),
        "position": pos,
        "waypoint_index": int(snap.get("waypoint_index", 0) or 0),
        "velocity_mps": float(snap.get("velocity_mps", 12.0) or 12.0),
        "battery_pct": float(snap.get("battery_pct", 100.0) or 100.0),
        "flight_phase": str(snap.get("flight_phase", "IDLE") or "IDLE"),
        "armed": bool(snap.get("armed", False)),
        "active": bool(snap.get("active", False)),
        "source": "mavlink_bridge_stub",
    }


def _control_stub_load_state(uav_id: str) -> Dict[str, Any]:
    all_state = _control_stub_state_all()
    row = all_state.get(uav_id)
    if isinstance(row, dict):
        return dict(row)
    return _control_stub_default_state(uav_id)


def _control_stub_store_state(uav_id: str, row: Dict[str, Any]) -> None:
    all_state = _control_stub_state_all()
    all_state[uav_id] = dict(row)
    _control_stub_save_all(all_state)


def _control_stub_apply_operation(*, uav_id: str, operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
    state = _control_stub_load_state(uav_id)
    pos = state.get("position") if isinstance(state.get("position"), dict) else {"x": 0.0, "y": 0.0, "z": 0.0}
    state["source"] = "mavlink_bridge_stub"

    if operation == "launch":
        state["armed"] = True
        state["active"] = True
        state["flight_phase"] = "TAKEOFF" if int(state.get("waypoint_index", 0) or 0) <= 0 else "MISSION"
    elif operation == "step":
        ticks = max(1, min(200, int(params.get("ticks", 1) or 1)))
        if bool(state.get("armed")) and bool(state.get("active")):
            velocity = max(0.5, float(state.get("velocity_mps", 12.0) or 12.0))
            dt_s = max(0.1, min(2.0, float(params.get("dt_s", 1.0) or 1.0)))
            distance = velocity * dt_s * ticks
            heading_deg = float(params.get("heading_deg", 30.0) or 30.0)
            heading_rad = heading_deg * (3.141592653589793 / 180.0)
            current_lon = float(pos.get("x", 0.0))
            current_lat = float(pos.get("y", 0.0))
            step_east_m = distance * math.cos(heading_rad)
            step_north_m = distance * math.sin(heading_rad)
            if -180.0 <= current_lon <= 180.0 and -90.0 <= current_lat <= 90.0:
                next_lon, next_lat = lon_lat_from_local_xy_m(
                    step_east_m,
                    step_north_m,
                    ref_lon=current_lon,
                    ref_lat=current_lat,
                )
                pos["x"] = float(next_lon)
                pos["y"] = float(next_lat)
            else:
                pos["x"] = current_lon + step_east_m
                pos["y"] = current_lat + step_north_m
            if str(state.get("flight_phase", "")).upper() == "TAKEOFF":
                pos["z"] = min(120.0, float(pos.get("z", 0.0)) + 2.0 * ticks)
                state["flight_phase"] = "MISSION" if float(pos.get("z", 0.0)) >= 20.0 else "TAKEOFF"
            state["position"] = point_with_aliases(
                {
                    "x": round(float(pos.get("x", 0.0)), 7),
                    "y": round(float(pos.get("y", 0.0)), 7),
                    "z": round(float(pos.get("z", 0.0)), 3),
                }
            )
            state["waypoint_index"] = int(state.get("waypoint_index", 0) or 0) + ticks
            state["battery_pct"] = max(0.0, float(state.get("battery_pct", 100.0) or 100.0) - 0.4 * ticks)
            if float(state.get("battery_pct", 0.0)) < 15.0:
                state["flight_phase"] = "LOW_BATTERY"
    elif operation == "hold":
        state["active"] = False
        state["flight_phase"] = "HOLD"
    elif operation == "resume":
        if bool(state.get("armed")):
            state["active"] = True
            state["flight_phase"] = "MISSION" if int(state.get("waypoint_index", 0) or 0) > 0 else "TAKEOFF"
    elif operation == "rth":
        state["active"] = False
        state["flight_phase"] = "RTH"
        sim = SIM.status_if_exists(uav_id) or {}
        waypoints = sim.get("waypoints") if isinstance(sim.get("waypoints"), list) else []
        home = waypoints[0] if isinstance(waypoints, list) and waypoints and isinstance(waypoints[0], dict) else {"x": 0.0, "y": 0.0, "z": 0.0}
        state["position"] = {
            **point_with_aliases(home),
        }
        state["waypoint_index"] = 0
    elif operation == "land":
        state["active"] = False
        state["armed"] = False
        state["flight_phase"] = "LAND"
        pos["z"] = 0.0
        state["position"] = point_with_aliases({"x": float(pos.get("x", 0.0)), "y": float(pos.get("y", 0.0)), "z": 0.0})

    state["position"] = point_with_aliases(
        {
            "x": round(float((state.get("position") or {}).get("x", 0.0)), 7),
            "y": round(float((state.get("position") or {}).get("y", 0.0)), 7),
            "z": round(float((state.get("position") or {}).get("z", 0.0)), 3),
        }
    )
    state["battery_pct"] = round(max(0.0, min(100.0, float(state.get("battery_pct", 100.0) or 100.0))), 2)
    _control_stub_store_state(uav_id, state)
    return state


def _demo_seed_uav_prefix(user_id: str) -> str:
    raw = str(user_id or "user").strip().lower()
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in raw)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")
    return cleaned or "user"


def _demo_seed_waypoints(*, x0: float, y0: float, z0: float, seed_idx: int) -> list[dict[str, Any]]:
    cruise = min(110.0, max(38.0, z0 + 38.0 + seed_idx * 2.0))
    if -180.0 <= x0 <= 180.0 and -90.0 <= y0 <= 90.0:
        return [
            point_with_aliases({"lon": x0, "lat": y0, "altM": z0, "action": "transit"}),
            point_with_aliases({"lon": x0 + 0.006, "lat": y0 + 0.004, "altM": cruise, "action": "transit"}),
            point_with_aliases({"lon": x0 + 0.012, "lat": y0 - 0.0035, "altM": min(120.0, cruise + 8.0), "action": "photo"}),
            point_with_aliases({"lon": x0 + 0.017, "lat": y0 + 0.0055, "altM": cruise, "action": "inspect"}),
        ]
    return [
        point_with_aliases({"x": x0, "y": y0, "z": z0, "action": "transit"}),
        point_with_aliases({"x": min(400.0, x0 + 40.0), "y": min(300.0, y0 + 30.0), "z": cruise, "action": "transit"}),
        point_with_aliases({"x": min(400.0, x0 + 95.0), "y": max(0.0, y0 - 18.0), "z": min(120.0, cruise + 8.0), "action": "photo"}),
        point_with_aliases({"x": min(400.0, x0 + 150.0), "y": min(300.0, y0 + 35.0), "z": cruise, "action": "inspect"}),
    ]


def _demo_seed_enabled() -> bool:
    raw = str(os.getenv("UAV_ENABLE_DEMO_SEED", "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@router.get("/api/uav/live/state")
@router.get("/api/uav/sim/state")
def get_sim_state(uav_id: str = "uav-1", operator_license_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    route = SIM.status_if_exists(uav_id)
    session = _get_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id)
    mission_defaults = _get_uav_mission_defaults(user_id=resolved_user_id, uav_id=uav_id)
    route_view = dict(route) if isinstance(route, dict) else {}
    if isinstance(session.get("utm_approval"), dict):
        route_view["utm_approval"] = dict(session["utm_approval"])
    if isinstance(session.get("utm_geofence_result"), dict):
        route_view["utm_geofence_result"] = dict(session["utm_geofence_result"])
    airspace = "sector-A3"
    current_waypoints = list(route_view.get("waypoints", [])) if isinstance(route_view.get("waypoints"), list) else []
    current_route_id = str(route_view.get("route_id", "route-1"))
    registry_row = _get_uav_registry_uav_row(uav_id)
    effective_license_id = (
        str(operator_license_id).strip()
        if operator_license_id is not None and str(operator_license_id).strip()
        else str(registry_row.get("operator_license_id", "op-001") or "op-001")
    )
    planned_start_at = (
        str(mission_defaults.get("planned_start_at", "")).strip()
        if isinstance(mission_defaults.get("planned_start_at"), str)
        else ""
    )
    planned_end_at = (
        str(mission_defaults.get("planned_end_at", "")).strip()
        if isinstance(mission_defaults.get("planned_end_at"), str)
        else ""
    )
    time_window_preview = UTM_SERVICE.check_time_window(
        planned_start_at=planned_start_at or None,
        planned_end_at=planned_end_at or None,
        operator_license_id=effective_license_id,
    )
    current_route_checks = {
        "uav_id": uav_id,
        "user_id": resolved_user_id,
        "route_id": current_route_id,
        "airspace_segment": airspace,
        "waypoints_total": len(current_waypoints),
        "geofence": _geofence_check_from_waypoints(
            uav_id=uav_id,
            route_id=current_route_id,
            airspace_segment=airspace,
            waypoints=current_waypoints,
        ) if len(current_waypoints) >= 1 else None,
        "no_fly_zone": UTM_SERVICE.check_no_fly_zones(current_waypoints) if len(current_waypoints) >= 1 else None,
        "time_window": time_window_preview,
        "planned_start_at": planned_start_at or None,
        "planned_end_at": planned_end_at or None,
        "operator_license_id": effective_license_id,
    }
    launch_gate_issues = _flight_control_gate_issues(uav_id, action="launch", user_id=resolved_user_id)
    step_gate_issues = _flight_control_gate_issues(uav_id, action="step", user_id=resolved_user_id)
    user_summary = _registry_user_summary(resolved_user_id)
    uav_registry_row = registry_row
    uav_registry_profile = _get_uav_registry_profile(uav_id)
    mission_paths = _latest_mission_paths_for(user_id=resolved_user_id, uav_id=uav_id)
    path_records = _path_records_summary_for(user_id=resolved_user_id, uav_id=uav_id)
    current_mission = _current_mission_record(user_id=resolved_user_id, uav_id=uav_id)
    station_state_all = UAV_DB.get_state("uav_station_state")
    station_row = (
        dict(station_state_all.get(uav_id))
        if isinstance(station_state_all, dict) and isinstance(station_state_all.get(uav_id), dict)
        else None
    )
    station_control_log = UAV_DB.get_state("uav_station_control_log")
    recent_station_controls: List[Dict[str, Any]] = []
    if isinstance(station_control_log, list):
        for rec in reversed(station_control_log):
            if not isinstance(rec, dict):
                continue
            if str(rec.get("uav_id", "")).strip() != uav_id:
                continue
            recent_station_controls.append(dict(rec))
            if len(recent_station_controls) >= 25:
                break
    return {
        "status": "success",
        "sync": UAV_DB.get_sync(),
        "dataSource": _uav_data_source_info(uav_id),
        "uav": route_view,
        "fleet": SIM.fleet_snapshot(),
        "session": {
            "scope": "user_uav",
            "user_id": resolved_user_id,
            "uav_id": uav_id,
            "utm_approval": session.get("utm_approval"),
            "utm_geofence_result": session.get("utm_geofence_result"),
            "utm_dss_result": session.get("utm_dss_result"),
            "updated_at": session.get("updated_at"),
        },
        "identity": {
            "selected_user_id": resolved_user_id,
            "selected_uav_id": uav_id,
            "uav_registry": uav_registry_row,
            "uav_registry_profile": uav_registry_profile,
        },
        "uav_registry_profile": uav_registry_profile,
        "uav_mission_defaults": mission_defaults,
        "mission_paths": mission_paths,
        "path_records": path_records,
        "current_mission": current_mission,
        "planned_route_history": _get_planned_route_history(),
        "latest_planned_routes": _latest_planned_routes_summary(),
        "uav_station_state": station_row,
        "uav_station_controls_recent": recent_station_controls,
        "uav_registry_user": user_summary,
        "flight_gate": {
            "launch_ready": len(launch_gate_issues) == 0,
            "launch_issues": launch_gate_issues,
            "step_ready": len(step_gate_issues) == 0,
            "step_issues": step_gate_issues,
        },
        "utm": {
            "weather": UTM_SERVICE.get_weather(airspace),
            "no_fly_zones": UTM_SERVICE.no_fly_zones,
            "regulations": UTM_SERVICE.regulations,
            "regulation_profiles": UTM_SERVICE.regulation_profiles,
            "effective_regulations": UTM_SERVICE.effective_regulations(effective_license_id),
            "licenses": UTM_SERVICE.operator_licenses,
            "current_route_checks": current_route_checks,
        },
    }


@router.get("/api/uav/registry/user")
def get_uav_registry_user(user_id: str = "user-1") -> Dict[str, Any]:
    result = _registry_user_summary(user_id)
    return {"status": "success", "sync": UAV_DB.get_sync(), "result": result}


@router.post("/api/uav/registry/assign")
def post_assign_uav_registry(payload: UavRegistryAssignPayload) -> Dict[str, Any]:
    user_id = _normalize_user_id(payload.user_id)
    uav_id = payload.uav_id.strip()
    if not uav_id:
        return {"status": "error", "error": "uav_id_required"}
    SIM.get_or_create(uav_id)
    _assign_uav_to_user(user_id=user_id, uav_id=uav_id, operator_license_id=payload.operator_license_id)
    result = _registry_user_summary(user_id)
    sync = _log_uav_action("registry_assign_uav", payload=payload.model_dump(), result={"user_id": user_id, "uav_id": uav_id}, entity_id=uav_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/uav/demo/seed-user-profiles")
def post_seed_demo_user_profiles(payload: UavDemoSeedPayload) -> Dict[str, Any]:
    if not _demo_seed_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "demo_seed_disabled",
                "message": "Set UAV_ENABLE_DEMO_SEED=1 to enable demo UAV profile seeding.",
            },
        )
    user_id = _normalize_user_id(payload.user_id)
    try:
        requested_count = int(payload.count)
    except Exception:
        requested_count = 3
    count = max(1, min(8, requested_count))
    prefix = _demo_seed_uav_prefix(user_id)
    license_ids = [str(k) for k in (UTM_SERVICE.operator_licenses or {}).keys() if str(k).strip()]
    if not license_ids:
        license_ids = ["op-001", "op-002", "op-003"]

    seeded_rows: list[dict[str, Any]] = []
    for idx in range(count):
        uav_id = f"uav-{prefix}-{idx + 1}"
        operator_license_id = license_ids[idx % len(license_ids)]
        x0 = float(24.816 + (idx * 0.008))
        y0 = float(60.180 + ((idx % 4) * 0.006))
        z0 = 35.0
        route_id = f"{uav_id}-demo-route-1"
        waypoints = _demo_seed_waypoints(x0=x0, y0=y0, z0=z0, seed_idx=idx)

        SIM.ingest_live_state(
            uav_id,
            position={"x": x0, "y": y0, "z": z0},
            route_id=route_id,
            source="simulated",
            source_meta={"created_by": "demo_seed", "user_id": user_id},
        )
        SIM.plan_route(uav_id, route_id=route_id, waypoints=waypoints)
        _assign_uav_to_user(user_id=user_id, uav_id=uav_id, operator_license_id=operator_license_id)

        profile_patch = {
            "uav_name": f"Demo UAV {idx + 1}",
            "uav_serial_number": f"SN-{prefix.upper()}-{idx + 1:03d}",
            "uav_registration_number": f"REG-{prefix.upper()}-{idx + 1:03d}",
            "manufacturer": "DemoDynamics",
            "model": f"D-{10 + idx}",
            "platform_type": "multirotor",
            "uav_category": "commercial",
            "uav_size_class": "middle",
            "max_takeoff_weight_kg": round(6.0 + idx * 0.8, 1),
            "empty_weight_kg": round(4.0 + idx * 0.6, 1),
            "payload_capacity_kg": round(2.0 + idx * 0.2, 1),
            "max_speed_mps_capability": round(17.0 + idx * 1.2, 1),
            "max_altitude_m": 120.0,
            "max_flight_time_min": float(34 + idx * 3),
            "battery_type": "Li-Ion",
            "battery_capacity_mah": float(16000 + idx * 1200),
            "remote_id_enabled": True,
            "remote_id": f"RID-{prefix.upper()}-{idx + 1:03d}",
            "c2_link_type": "5g" if idx % 2 == 0 else "lte",
            "launch_site_id": f"{user_id}-launch-{idx + 1}",
            "landing_site_id": f"{user_id}-landing-{idx + 1}",
            "contingency_action": "rth",
            "weather_min_visibility_km": 3.0,
            "weather_max_wind_mps": round(11.0 + idx * 0.4, 1),
            "home_base_id": f"{user_id}-home",
            "home_x": x0,
            "home_y": y0,
            "home_z": z0,
            "status": "active",
            "firmware_version": f"v2.1.{idx}",
            "airworthiness_status": "airworthy",
            "owner_org_id": f"org-{prefix}",
            "owner_name": user_id,
            "notes": "Auto-seeded demo UAV profile for UI demonstration.",
        }
        saved_profile = _save_uav_registry_profile(user_id=user_id, uav_id=uav_id, patch=profile_patch)
        mission_defaults = _save_uav_mission_defaults(
            user_id=user_id,
            uav_id=uav_id,
            patch={
                "route_id": route_id,
                "airspace_segment": "sector-A3",
                "requested_speed_mps": round(12.0 + idx * 0.8, 1),
                "hold_reason": "operator_request",
                "mission_priority": "normal" if idx < 2 else "urgent",
                "operation_type": "inspection",
                "c2_link_type": "5g" if idx % 2 == 0 else "lte",
            },
        )
        _record_planned_route_history(
            uav_id=uav_id,
            route_id=route_id,
            waypoints=waypoints,
            source="plan",
            metadata={"user_id": user_id, "route_category": "user_planned", "seeded_demo": True},
        )
        seeded_rows.append(
            {
                "user_id": user_id,
                "uav_id": uav_id,
                "operator_license_id": operator_license_id,
                "route_id": route_id,
                "registry_profile": saved_profile,
                "mission_defaults": mission_defaults,
                "sim_status": SIM.status(uav_id),
            }
        )

    demo_row = seeded_rows[0] if seeded_rows else None
    result = {
        "user_id": user_id,
        "seeded_count": len(seeded_rows),
        "seeded_uavs": seeded_rows,
        "demo": demo_row,
        "uav_registry_user": _registry_user_summary(user_id),
    }
    sync = _log_uav_action(
        "demo_seed_user_profiles",
        payload={"user_id": user_id, "count": count},
        result={"seeded_count": len(seeded_rows), "demo_uav_id": (demo_row or {}).get("uav_id")},
        entity_id=(demo_row or {}).get("uav_id"),
    )
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/uav/registry/profile")
def post_uav_registry_profile(payload: UavRegistryProfilePayload) -> Dict[str, Any]:
    uav_id = payload.uav_id.strip()
    if not uav_id:
        return {"status": "error", "error": "uav_id_required"}
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=payload.user_id)
    SIM.get_or_create(uav_id)
    _assign_uav_to_user(user_id=resolved_user_id, uav_id=uav_id)
    patch = payload.model_dump(exclude_unset=True)
    patch.pop("user_id", None)
    patch.pop("uav_id", None)
    profile = _save_uav_registry_profile(user_id=resolved_user_id, uav_id=uav_id, patch=patch)
    result = {
        "user_id": resolved_user_id,
        "uav_id": uav_id,
        "registry_profile": profile,
        "uav_registry_user": _registry_user_summary(resolved_user_id),
    }
    sync = _log_uav_action("registry_update_profile", payload={**payload.model_dump(), "user_id": resolved_user_id}, result={"uav_id": uav_id}, entity_id=uav_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/uav/mission-defaults")
def post_uav_mission_defaults(payload: UavMissionDefaultsPayload) -> Dict[str, Any]:
    uav_id = payload.uav_id.strip()
    if not uav_id:
        return {"status": "error", "error": "uav_id_required"}
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=payload.user_id)
    SIM.get_or_create(uav_id)
    _assign_uav_to_user(user_id=resolved_user_id, uav_id=uav_id)
    patch = payload.model_dump(exclude_unset=True)
    patch.pop("user_id", None)
    patch.pop("uav_id", None)
    mission_defaults = _save_uav_mission_defaults(user_id=resolved_user_id, uav_id=uav_id, patch=patch)
    result = {"user_id": resolved_user_id, "uav_id": uav_id, "mission_defaults": mission_defaults}
    sync = _log_uav_action("uav_update_mission_defaults", payload={**payload.model_dump(), "user_id": resolved_user_id}, result={"uav_id": uav_id}, entity_id=uav_id)
    return {"status": "success", "sync": sync, "result": result}


@router.get("/api/uav/sim/fleet")
def get_sim_fleet() -> Dict[str, Any]:
    return {"status": "success", "sync": UAV_DB.get_sync(), "result": {"fleet": SIM.fleet_snapshot()}}


@router.post("/api/uav/sim/fleet/reset-all")
def post_reset_sim_fleet_all(
    clear_registry: bool = True,
    clear_history: bool = True,
    clear_utm_artifacts: bool = True,
) -> Dict[str, Any]:
    existing = SIM.fleet_snapshot()
    deleted_uav_ids = [str(uid) for uid in existing.keys() if str(uid).strip()]

    for uid in deleted_uav_ids:
        SIM.delete_uav(uid)

    if clear_history:
        UAV_DB.set_state("planned_route_history", {})
        UAV_DB.set_state("approved_flight_path_history", {})
        _set_mission_records(UAV_DB, {"by_scope": {}, "by_id": {}})
        UTM_DB_MIRROR.set_state("approved_flight_path_history", {})
        _set_mission_records(UTM_DB_MIRROR, {"by_scope": {}, "by_id": {}})
        UAV_DB.set_state("uav_station_state", {})
        UAV_DB.set_state("uav_station_control_log", [])

    _set_uav_utm_sessions({})
    UTM_SERVICE.approvals = {}

    if clear_utm_artifacts:
        _set_local_dss_operational_intents({})
        UTM_DB_MIRROR.set_state("dss_notifications", [])
        UTM_DB_MIRROR.set_state("dss_subscriptions", {})

    if clear_registry:
        registry = _get_uav_registry()
        users = registry.get("users") if isinstance(registry.get("users"), dict) else {}
        cleaned_users: Dict[str, Dict[str, Any]] = {}
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for user_id, row in users.items():
            if not isinstance(user_id, str):
                continue
            next_row = dict(row) if isinstance(row, dict) else {}
            next_row["uav_ids"] = []
            next_row["updated_at"] = now_iso
            cleaned_users[user_id] = next_row
        if not cleaned_users:
            cleaned_users = {"user-1": {"uav_ids": [], "updated_at": now_iso}}
        _set_uav_registry({"users": cleaned_users, "uavs": {}})

    NETWORK_MISSION_SERVICE.tracked_uavs = {}
    NETWORK_MISSION_SERVICE.latest_live_telemetry = None

    result = {
        "deleted_count": len(deleted_uav_ids),
        "deleted_uav_ids": deleted_uav_ids,
        "fleet": SIM.fleet_snapshot(),
        "latest_planned_routes": _latest_planned_routes_summary(),
        "history_cleared": bool(clear_history),
        "registry_cleared": bool(clear_registry),
        "utm_artifacts_cleared": bool(clear_utm_artifacts),
    }
    sync = _log_uav_action(
        "fleet_reset_all",
        payload={
            "clear_registry": bool(clear_registry),
            "clear_history": bool(clear_history),
            "clear_utm_artifacts": bool(clear_utm_artifacts),
        },
        result=result,
    )
    return {"status": "success", "sync": sync, "result": result}


@router.get("/api/uav/sync")
def get_uav_sync(limit_actions: int = 5) -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "sync": UAV_DB.get_sync(),
            "recentActions": UAV_DB.recent_actions(limit_actions),
        },
    }


@router.get("/api/uav/live/source")
def get_uav_live_source(uav_id: str = "uav-1") -> Dict[str, Any]:
    return {"status": "success", "sync": UAV_DB.get_sync(), "result": _uav_data_source_info(uav_id)}


@router.get("/api/uav/live/control-adapter")
def get_uav_control_adapter(uav_id: str = "uav-1") -> Dict[str, Any]:
    return {
        "status": "success",
        "sync": UAV_DB.get_sync(),
        "result": get_uav_control_adapter_status(uav_id=uav_id),
    }


@router.get("/api/uav/control/_contract")
def get_uav_control_contract() -> Dict[str, Any]:
    request_example = {
        "uav_id": "uav-1",
        "operation": "launch",
        "params": {"ticks": 1},
        "requested_at": "2026-02-28T12:30:00Z",
        "command_id": "cmd-uav-1-launch-001",
        "idempotency_key": "mission-123-launch",
        "caller": "mission_supervisor",
    }
    response_example = {
        "status": "success",
        "adapter": dict(_CONTROL_BRIDGE_STUB_ADAPTER),
        "command": {
            "command_id": "cmd-uav-1-launch-001",
            "uav_id": "uav-1",
            "operation": "launch",
            "requested_at": "2026-02-28T12:30:00Z",
            "accepted_at": "2026-02-28T12:30:00Z",
            "idempotency_key": "mission-123-launch",
        },
        "telemetry": {
            "route_id": "route-1",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "waypoint_index": 0,
            "velocity_mps": 12.0,
            "battery_pct": 99.6,
            "flight_phase": "TAKEOFF",
            "armed": True,
            "active": True,
            "source": "mavlink_bridge_stub",
        },
        "result": {
            "uplink": {
                "accepted": True,
                "transport": "mavlink_stub",
                "link": "udp://127.0.0.1:14550",
            }
        },
    }
    return {
        "status": "success",
        "result": {
            "path": "/api/uav/control/{operation}",
            "operations": sorted(list({"launch", "step", "hold", "resume", "rth", "land"})),
            "request_schema": UavControlBridgeCommandPayload.model_json_schema(),
            "response_schema": UavControlBridgeResponsePayload.model_json_schema(),
            "examples": {
                "request": request_example,
                "response": response_example,
            },
            "notes": [
                "This endpoint is a sample MAVLink bridge stub for local integration testing.",
                "Production bridge should execute real MAVLink/DJI uplink and return live telemetry in `telemetry`.",
            ],
        },
    }


@router.post("/api/uav/control/{operation}")
def post_uav_control_bridge(operation: str, payload: UavControlBridgeCommandPayload) -> Dict[str, Any]:
    supported, op = parse_control_operation(operation)
    if not supported:
        raise HTTPException(status_code=404, detail={"status": "error", "error": f"unsupported_operation:{operation}"})
    payload_op = str(payload.operation or "").strip().lower()
    if payload_op and payload_op != op:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "error": "operation_mismatch",
                "details": f"path operation `{op}` does not match payload.operation `{payload_op}`",
            },
        )
    accepted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    cmd_id = str(payload.command_id or f"cmd-{payload.uav_id}-{op}-{accepted_at}").replace(":", "").replace(".", "")
    telemetry = _control_stub_apply_operation(
        uav_id=payload.uav_id,
        operation=op,
        params=dict(payload.params or {}),
    )
    body: Dict[str, Any] = {
        "status": "success",
        "adapter": dict(_CONTROL_BRIDGE_STUB_ADAPTER),
        "command": {
            "command_id": cmd_id,
            "uav_id": payload.uav_id,
            "operation": op,
            "requested_at": str(payload.requested_at or accepted_at),
            "accepted_at": accepted_at,
            "idempotency_key": payload.idempotency_key,
            "caller": payload.caller,
        },
        "telemetry": {
            "route_id": str(telemetry.get("route_id", "route-1")),
            "position": dict(telemetry.get("position") or {"x": 0.0, "y": 0.0, "z": 0.0}),
            "waypoint_index": int(telemetry.get("waypoint_index", 0) or 0),
            "velocity_mps": float(telemetry.get("velocity_mps", 0.0) or 0.0),
            "battery_pct": float(telemetry.get("battery_pct", 0.0) or 0.0),
            "flight_phase": str(telemetry.get("flight_phase", "UNKNOWN")),
            "armed": bool(telemetry.get("armed", False)),
            "active": bool(telemetry.get("active", False)),
            "source": str(telemetry.get("source", "mavlink_bridge_stub")),
        },
        "result": {
            "uplink": {
                "accepted": True,
                "transport": "mavlink_stub",
                "link": "udp://127.0.0.1:14550",
                "mode": "GUIDED" if op in {"launch", "resume", "step"} else ("RTL" if op == "rth" else "LOITER"),
            }
        },
    }
    sync = _log_uav_action(
        "control_bridge_stub_command",
        payload={
            "operation": op,
            **payload.model_dump(),
        },
        result={
            "uav_id": payload.uav_id,
            "operation": op,
            "command_id": cmd_id,
            "flight_phase": str((body.get("telemetry") or {}).get("flight_phase")),
        },
        entity_id=payload.uav_id,
    )
    body["sync"] = sync
    return body


@router.post("/api/uav/live/ingest")
def post_uav_live_ingest(payload: UavLiveTelemetryPayload) -> Dict[str, Any]:
    waypoints = [_dump_waypoint_payload_model(w) for w in payload.waypoints] if payload.waypoints else None
    pos = payload.position.model_dump() if payload.position else None
    source_meta = {
        "source_ref": payload.source_ref,
        "observed_at": payload.observed_at,
        "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    snap = SIM.ingest_live_state(
        payload.uav_id,
        route_id=payload.route_id,
        waypoints=waypoints,
        position=pos,  # type: ignore[arg-type]
        waypoint_index=payload.waypoint_index,
        velocity_mps=payload.velocity_mps,
        battery_pct=payload.battery_pct,
        flight_phase=payload.flight_phase,
        armed=payload.armed,
        active=payload.active,
        source=payload.source,
        source_meta=source_meta,
    )
    result = {"uav": snap, "dataSource": _uav_data_source_info(payload.uav_id)}
    sync = _log_uav_action("uav_live_ingest", payload=payload.model_dump(), result={"dataSource": result["dataSource"]}, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/uav/live/plan")
@router.post("/api/uav/sim/plan")
def post_plan_route(payload: PlanRoutePayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    waypoints = [_dump_waypoint_payload_model(w) for w in payload.waypoints]
    result = uav_plan_route.invoke(
        {
            "uav_id": payload.uav_id,
            "route_id": payload.route_id,
            "waypoints": waypoints,
        }
    )
    _record_planned_route_history(
        uav_id=payload.uav_id,
        route_id=payload.route_id,
        waypoints=waypoints,
        source="plan",
        metadata={"user_id": resolved_user_id, "route_category": "user_planned"},
    )
    sim_now = SIM.status(payload.uav_id)
    lifecycle_route_id = str(sim_now.get("route_id", payload.route_id) or payload.route_id)
    mission_defaults = _get_uav_mission_defaults(user_id=resolved_user_id, uav_id=payload.uav_id)
    lifecycle_airspace = str(mission_defaults.get("airspace_segment") or "sector-A3")
    lifecycle_waypoints = (
        [dict(w) for w in sim_now.get("waypoints", []) if isinstance(w, dict)]
        if isinstance(sim_now.get("waypoints"), list)
        else [dict(w) for w in waypoints if isinstance(w, dict)]
    )
    dss_intent_result = _upsert_local_dss_intent_for_uav(
        user_id=resolved_user_id,
        uav_id=payload.uav_id,
        route_id=lifecycle_route_id,
        waypoints=lifecycle_waypoints,
        airspace_segment=lifecycle_airspace,
        state="accepted",
        conflict_policy="conditional_approve",
        source="plan_route",
        lifecycle_phase="planned",
    )
    _save_uav_utm_session(
        user_id=resolved_user_id,
        uav_id=payload.uav_id,
        utm_dss_result=dss_intent_result if isinstance(dss_intent_result, dict) else None,
    )
    sync = _log_uav_action("plan_route", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
        result["session"] = _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        result["dss_intent_result"] = dss_intent_result
    return result


@router.post("/api/uav/sim/fleet/add")
def post_add_sim_uav(payload: FleetCreateUavPayload) -> Dict[str, Any]:
    existing = SIM.fleet_snapshot()
    if payload.uav_id and payload.uav_id.strip():
        uav_id = payload.uav_id.strip()
    else:
        i = 1
        while f"uav-{i}" in existing:
            i += 1
        uav_id = f"uav-{i}"
    lon = float(payload.lon if payload.lon is not None else payload.x)
    lat = float(payload.lat if payload.lat is not None else payload.y)
    z = max(0.0, min(120.0, float(payload.altM if payload.altM is not None else payload.z)))
    x, y = _coerce_add_uav_lon_lat(lon, lat)
    SIM.ingest_live_state(
        uav_id,
        position=point_with_aliases({"lon": x, "lat": y, "altM": z}),
        route_id=f"{uav_id}-route-1",
        source="simulated",
        source_meta={"created_by": "ui_map_add"},
    )
    reset_route = _generate_reset_route_from_position(point_with_aliases({"lon": x, "lat": y, "altM": z}))
    SIM.plan_route(uav_id, route_id=f"{uav_id}-route-1", waypoints=reset_route)
    _assign_uav_to_user(
        user_id=_normalize_user_id(payload.user_id),
        uav_id=uav_id,
        operator_license_id=payload.operator_license_id,
    )
    _record_planned_route_history(uav_id=uav_id, route_id=f"{uav_id}-route-1", waypoints=reset_route, source="create_uav")
    snap = SIM.status(uav_id)
    sync = _log_uav_action("fleet_add_uav", payload=payload.model_dump(), result={"uav": snap}, entity_id=uav_id)
    return {"status": "success", "sync": sync, "result": {"uav": snap, "fleet": SIM.fleet_snapshot(), "latest_planned_routes": _latest_planned_routes_summary()}}


@router.post("/api/uav/sim/fleet/delete")
def post_delete_sim_uav(payload: FleetDeleteUavPayload) -> Dict[str, Any]:
    uav_id = payload.uav_id.strip()
    if not uav_id:
        return {"status": "error", "error": "uav_id_required"}
    existed = SIM.delete_uav(uav_id)
    _delete_planned_route_history(uav_id)
    _remove_uav_from_registry(uav_id)
    cleanup = _cleanup_deleted_uav_artifacts(uav_id)
    result = {
        "uav_id": uav_id,
        "deleted": bool(existed),
        "cleanup": cleanup,
        "fleet": SIM.fleet_snapshot(),
        "latest_planned_routes": _latest_planned_routes_summary(),
    }
    sync = _log_uav_action("fleet_delete_uav", payload=payload.model_dump(), result=result, entity_id=uav_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/uav/sim/reset-route")
def post_reset_sim_route(payload: ResetRoutePayload) -> Dict[str, Any]:
    snap_before = SIM.status(payload.uav_id)
    route_id = payload.route_id or f"{payload.uav_id}-route-{int(datetime.now(timezone.utc).timestamp())}"
    reset_route = _generate_reset_route_from_position(snap_before.get("position") if isinstance(snap_before.get("position"), dict) else {})
    snap = SIM.plan_route(payload.uav_id, route_id=route_id, waypoints=reset_route)
    _record_planned_route_history(uav_id=payload.uav_id, route_id=route_id, waypoints=reset_route, source="reset_route")
    sync = _log_uav_action("reset_route", payload=payload.model_dump(), result={"uav": snap}, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": {"uav": snap, "route_id": route_id, "waypoints": reset_route}}


@router.post("/api/uav/path-records/delete")
def post_delete_path_record(payload: PathRecordDeletePayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    category = str(payload.category or "").strip()
    if category not in {"user_planned", "agent_replanned", "dss_replanned", "utm_confirmed"}:
        return {
            "status": "error",
            "error": "invalid_category",
            "allowed": ["user_planned", "agent_replanned", "dss_replanned", "utm_confirmed"],
        }
    deleted = False
    details: Dict[str, Any] = {"category": category, "user_id": resolved_user_id, "uav_id": payload.uav_id}
    if category in {"user_planned", "agent_replanned", "dss_replanned"}:
        deleted = _delete_planned_route_history_category(uav_id=payload.uav_id, category=category)
        details["uav_planned_history_deleted"] = deleted
    else:
        deleted_uav = _delete_approved_flight_path_for_scope(
            db=UAV_DB, state_key="approved_flight_path_history", user_id=resolved_user_id, uav_id=payload.uav_id
        )
        deleted_utm = _delete_approved_flight_path_for_scope(
            db=UTM_DB_MIRROR, state_key="approved_flight_path_history", user_id=resolved_user_id, uav_id=payload.uav_id
        )
        deleted = deleted_uav or deleted_utm
        details["uav_approved_history_deleted"] = deleted_uav
        details["utm_approved_history_deleted"] = deleted_utm
    details["deleted"] = deleted
    details["mission_paths"] = _latest_mission_paths_for(user_id=resolved_user_id, uav_id=payload.uav_id)
    details["path_records"] = _path_records_summary_for(user_id=resolved_user_id, uav_id=payload.uav_id)
    sync = _log_uav_action("delete_path_record", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=details, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": details}


@router.post("/api/uav/live/replan-via-utm-nfz")
@router.post("/api/uav/sim/replan-via-utm-nfz")
def post_replan_via_utm_nfz(payload: ReplanPayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    max_auto_rounds = 4
    route_category_raw = str(payload.route_category or "").strip().lower()
    replan_context_raw = str(payload.replan_context or "").strip().lower()
    if route_category_raw in {"agent_replanned", "dss_replanned"}:
        route_category = route_category_raw
    elif replan_context_raw in {"dss_conflict", "dss_conflict_mitigation", "dss_strategic_conflict"}:
        route_category = "dss_replanned"
    else:
        route_category = "agent_replanned"
    replan_context = (
        replan_context_raw
        if replan_context_raw
        else ("dss_conflict_mitigation" if route_category == "dss_replanned" else "general")
    )

    def _count_inserted_waypoints(waypoints: list[dict]) -> int:
        return sum(1 for w in waypoints if isinstance(w, dict) and str(w.get("_wp_origin", "")) == "agent_inserted")

    def _route_signature(waypoints: list[dict]) -> str:
        compact: list[str] = []
        for w in waypoints:
            if not isinstance(w, dict):
                continue
            compact.append(
                f"{round(float(w.get('x', 0.0)),2)}:{round(float(w.get('y', 0.0)),2)}:{round(float(w.get('z', 0.0)),2)}:"
                f"{str(w.get('action','transit'))}:{str(w.get('_wp_origin',''))}"
            )
        return "|".join(compact)

    def _utm_verify_retryable_conflict(verify: Dict[str, Any] | None) -> bool:
        if not isinstance(verify, dict):
            return False
        checks = verify.get("checks") if isinstance(verify.get("checks"), dict) else {}
        if not isinstance(checks, dict):
            return False
        route_bounds = checks.get("route_bounds") if isinstance(checks.get("route_bounds"), dict) else {}
        nfz = checks.get("no_fly_zone") if isinstance(checks.get("no_fly_zone"), dict) else {}
        route_bounds_ok = route_bounds.get("ok")
        if route_bounds_ok is None:
            route_bounds_ok = route_bounds.get("geofence_ok")
        if route_bounds_ok is None:
            route_bounds_ok = route_bounds.get("bounds_ok")
        nfz_ok = nfz.get("ok")
        return bool(route_bounds_ok is False or nfz_ok is False)

    def _invoke_replan(*, user_request: str, route_id: Optional[str], waypoints: Optional[list[dict]]) -> Dict[str, Any]:
        invoke_payload = {
            "uav_id": payload.uav_id,
            "airspace_segment": payload.airspace_segment,
            "user_request": user_request,
            "route_id": route_id,
            "waypoints": waypoints,
            "optimization_profile": payload.optimization_profile,
        }
        return uav_replan_route_via_utm_nfz.invoke(invoke_payload)

    result = _invoke_replan(
        user_request=payload.user_request,
        route_id=payload.route_id,
        waypoints=[_dump_waypoint_payload_model(w) for w in payload.waypoints] if payload.waypoints else None,
    )
    utm_verify: Dict[str, Any] | None = None
    approved_route_records: Dict[str, Any] | None = None
    auto_replan_utm_loop: list[Dict[str, Any]] = []
    if isinstance(result, dict) and result.get("status") == "success":
        seen_signatures: set[str] = set()
        registry_row = _get_uav_registry_uav_row(payload.uav_id)
        lic_id = (
            str(payload.operator_license_id).strip()
            if payload.operator_license_id and str(payload.operator_license_id).strip()
            else str(registry_row.get("operator_license_id", "op-001") or "op-001")
        )
        lic_rec = UTM_SERVICE.operator_licenses.get(lic_id, {})
        required_class = str(lic_rec.get("license_class", "VLOS")) if isinstance(lic_rec, dict) else "VLOS"

        round_index = 0
        while isinstance(result, dict) and result.get("status") == "success" and round_index < max_auto_rounds:
            round_index += 1
            rr = result.get("result")
            if not isinstance(rr, dict):
                break
            route_id = str(rr.get("route_id", payload.route_id or "route-1"))
            uav = rr.get("uav")
            if not (isinstance(uav, dict) and isinstance(uav.get("waypoints"), list)):
                break
            verify_waypoints = [dict(w) for w in uav["waypoints"] if isinstance(w, dict)]
            waypoint_deletions = rr.get("waypoint_deletions") if isinstance(rr.get("waypoint_deletions"), list) else []
            los_prune_deletions = rr.get("los_prune_deletions") if isinstance(rr.get("los_prune_deletions"), list) else []
            los_prune_passes = rr.get("los_prune_passes") if isinstance(rr.get("los_prune_passes"), list) else []
            inserted_trim_deletions = rr.get("inserted_trim_deletions") if isinstance(rr.get("inserted_trim_deletions"), list) else []
            inserted_trim_passes = rr.get("inserted_trim_passes") if isinstance(rr.get("inserted_trim_passes"), list) else []
            replaced_original_waypoints = rr.get("replaced_original_waypoints") if isinstance(rr.get("replaced_original_waypoints"), list) else []
            inserted_count = _count_inserted_waypoints(verify_waypoints)
            sig = _route_signature(verify_waypoints)
            seen_before = sig in seen_signatures
            seen_signatures.add(sig)
            _record_planned_route_history(
                uav_id=payload.uav_id,
                route_id=route_id,
                waypoints=verify_waypoints,
                source="replan_via_utm_nfz",
                metadata={
                    "route_category": route_category,
                    "replan_context": replan_context,
                    "dss_conflict_mitigation": route_category == "dss_replanned",
                    "optimization_profile": payload.optimization_profile,
                    "user_id": resolved_user_id,
                    "replan_round_index": round_index,
                    "waypoint_deletions_count": len(waypoint_deletions),
                    "los_prune_deletions_count": len(los_prune_deletions),
                    "los_prune_passes_count": len(los_prune_passes),
                    "los_prune_passes": [dict(p) for p in los_prune_passes if isinstance(p, dict)],
                    "inserted_trim_deletions_count": len(inserted_trim_deletions),
                    "inserted_trim_passes_count": len(inserted_trim_passes),
                    "inserted_trim_passes": [dict(p) for p in inserted_trim_passes if isinstance(p, dict)],
                    "replaced_original_waypoints_count": len(replaced_original_waypoints),
                    "inserted_waypoints_count": inserted_count,
                },
            )

            approved = False
            retryable_conflict = False
            if payload.auto_utm_verify:
                utm_verify = UTM_SERVICE.verify_flight_plan(
                    uav_id=payload.uav_id,
                    airspace_segment=payload.airspace_segment,
                    route_id=route_id,
                    waypoints=verify_waypoints,
                    operator_license_id=lic_id,
                    required_license_class=required_class or "VLOS",
                    requested_speed_mps=float(uav.get("velocity_mps", 12.0) or 12.0),
                )
                if isinstance(utm_verify, dict):
                    approved = bool(utm_verify.get("approved"))
                    retryable_conflict = _utm_verify_retryable_conflict(utm_verify)
                    SIM.set_approval(payload.uav_id, utm_verify)
                    checks = utm_verify.get("checks") if isinstance(utm_verify.get("checks"), dict) else {}
                    route_bounds = checks.get("route_bounds") if isinstance(checks, dict) and isinstance(checks.get("route_bounds"), dict) else {}
                    nfz = checks.get("no_fly_zone") if isinstance(checks, dict) and isinstance(checks.get("no_fly_zone"), dict) else {}
                    geofence_ok = route_bounds.get("ok")
                    if geofence_ok is None:
                        geofence_ok = route_bounds.get("geofence_ok")
                    if geofence_ok is None:
                        geofence_ok = route_bounds.get("bounds_ok")
                    session_geofence = {
                        "ok": bool(geofence_ok is True and (nfz.get("ok") is True or nfz.get("ok") is None)),
                        "geofence_ok": geofence_ok is True,
                        "bounds_ok": geofence_ok is True,
                        "airspace_segment": payload.airspace_segment,
                        "no_fly_zone": dict(nfz) if isinstance(nfz, dict) else None,
                    }
                    SIM.set_geofence_result(payload.uav_id, session_geofence)
                    _save_uav_utm_session(
                        user_id=resolved_user_id,
                        uav_id=payload.uav_id,
                        utm_approval=utm_verify,
                        utm_geofence_result=session_geofence,
                    )
                    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
                    if approved:
                        mission_save = _save_verified_mission_and_paths(
                            user_id=resolved_user_id,
                            uav_id=payload.uav_id,
                            route_id=route_id,
                            waypoints=verify_waypoints,
                            approval=utm_verify,
                            source="replan_via_utm_nfz",
                            copy_agent_if_missing=False,
                        )
                        if isinstance(mission_save, dict):
                            approved_route_records = mission_save.get("approved_route_records") if isinstance(mission_save.get("approved_route_records"), dict) else None
                            if approved_route_records:
                                rr["mission"] = mission_save.get("mission")

            auto_replan_utm_loop.append(
                {
                    "round": round_index,
                    "route_id": route_id,
                    "waypoints_total": len(verify_waypoints),
                    "inserted_waypoints_count": inserted_count,
                    "los_prune_deletions_count": len(los_prune_deletions),
                    "approved": approved if payload.auto_utm_verify else None,
                    "retryable_conflict": retryable_conflict if payload.auto_utm_verify else None,
                    "route_repeated": seen_before,
                }
            )

            rr["utm_verify"] = utm_verify
            rr["approved_route_records"] = approved_route_records
            rr["auto_replan_utm_loop"] = auto_replan_utm_loop
            rr["route_category"] = route_category
            rr["replan_context"] = replan_context

            if not payload.auto_utm_verify or approved:
                break
            if not retryable_conflict or seen_before:
                break

            conflict_fb = _utm_nfz_conflict_feedback(utm_verify)
            corrective_prompt = (
                f"{payload.user_request}. Continue two-step replanning: insert detour points, then prune inserted points with LoS. "
                f"Must pass UTM checks and minimize inserted waypoints. "
                f"{('Resolve remaining conflict at ' + conflict_fb['summary'] + '.') if conflict_fb.get('summary') else ''}"
            ).strip()
            sim_now = SIM.status(payload.uav_id)
            current_waypoints = [dict(w) for w in sim_now.get("waypoints", [])] if isinstance(sim_now.get("waypoints"), list) else verify_waypoints
            result = _invoke_replan(
                user_request=corrective_prompt,
                route_id=str(sim_now.get("route_id", route_id)),
                waypoints=current_waypoints,
            )
    sync = _log_uav_action(
        "replan_via_utm_nfz",
        payload={**payload.model_dump(), "user_id": resolved_user_id},
        result=result,
        entity_id=payload.uav_id,
    )
    if isinstance(result, dict):
        result["sync"] = sync
        result["session"] = _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        result["route_category"] = route_category
        result["replan_context"] = replan_context
    return result


@router.post("/api/uav/agent/chat")
def post_uav_agent_chat(payload: UavAgentChatPayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=None)
    prompt = (payload.prompt or "").strip()
    sim_before = SIM.status(payload.uav_id)
    route_id = payload.route_id or str(sim_before.get("route_id", "route-1"))
    input_waypoints = [_dump_waypoint_payload_model(w) for w in payload.waypoints] if payload.waypoints else None
    effective_waypoints = input_waypoints if input_waypoints else (
        list(sim_before.get("waypoints", [])) if isinstance(sim_before.get("waypoints"), list) else []
    )
    if len(effective_waypoints) < 2:
        result = {"status": "error", "error": "route_requires_at_least_two_waypoints"}
        sync = _log_uav_action("agent_chat", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=result, entity_id=payload.uav_id)
        return {"status": "error", "sync": sync, "result": result}
    SIM.plan_route(payload.uav_id, route_id=route_id, waypoints=effective_waypoints)

    def _apply_agent_chat_side_effects(agent_result: Dict[str, Any]) -> None:
        uav_after = agent_result.get("uav") if isinstance(agent_result.get("uav"), dict) else {}
        after_waypoints = [dict(w) for w in uav_after.get("waypoints", [])] if isinstance(uav_after.get("waypoints"), list) else []
        after_route_id = str(uav_after.get("route_id", route_id) or route_id)
        replan_rec = agent_result.get("replan") if isinstance(agent_result.get("replan"), dict) else {}
        replan_status = str(replan_rec.get("status", "") or "").strip().lower()
        tool_trace = agent_result.get("toolTrace") if isinstance(agent_result.get("toolTrace"), list) else []
        prompt_l = prompt.lower()
        dss_prompt_context = ("dss" in prompt_l) and any(k in prompt_l for k in ("conflict", "strategic", "blocking"))
        agent_replan_context = str(agent_result.get("replanContext", "") or "").strip().lower()
        dss_context_from_result = "dss" in agent_replan_context and "conflict" in agent_replan_context
        dss_context_from_trace = False
        for rec in tool_trace:
            if not isinstance(rec, dict):
                continue
            if str(rec.get("tool", "")).strip() != "uav_replan_route_via_utm_nfz":
                continue
            reason = str(rec.get("reason", "") or "").strip().lower()
            replan_ctx = str(rec.get("replan_context", "") or "").strip().lower()
            if ("dss" in reason and ("conflict" in reason or "strategic" in reason)) or (
                "dss" in replan_ctx and "conflict" in replan_ctx
            ):
                dss_context_from_trace = True
                break
        route_category = "dss_replanned" if (dss_prompt_context or dss_context_from_result or dss_context_from_trace) else "agent_replanned"
        replan_context = "dss_conflict_mitigation" if route_category == "dss_replanned" else "agent_copilot"
        trace_replan_ok = any(
            isinstance(t, dict)
            and str(t.get("tool", "")).strip() == "uav_replan_route_via_utm_nfz"
            and str(t.get("status", "")).strip().lower() == "success"
            for t in tool_trace
        )
        if after_waypoints and (replan_status == "success" or trace_replan_ok):
            _record_planned_route_history(
                uav_id=payload.uav_id,
                route_id=after_route_id,
                waypoints=after_waypoints,
                source="agent_copilot",
                metadata={
                    "route_category": route_category,
                    "replan_context": replan_context,
                    "auto_verify": payload.auto_verify,
                    "optimization_profile": payload.optimization_profile,
                    "user_id": resolved_user_id,
                },
            )
        utm_verify = agent_result.get("utmVerify") if isinstance(agent_result.get("utmVerify"), dict) else None
        if not isinstance(utm_verify, dict):
            return
        SIM.set_approval(payload.uav_id, utm_verify)
        checks = utm_verify.get("checks") if isinstance(utm_verify.get("checks"), dict) else {}
        route_bounds = checks.get("route_bounds") if isinstance(checks, dict) and isinstance(checks.get("route_bounds"), dict) else {}
        nfz = checks.get("no_fly_zone") if isinstance(checks, dict) and isinstance(checks.get("no_fly_zone"), dict) else {}
        geofence_ok = route_bounds.get("ok")
        if geofence_ok is None:
            geofence_ok = route_bounds.get("geofence_ok")
        if geofence_ok is None:
            geofence_ok = route_bounds.get("bounds_ok")
        session_geofence = {
            "ok": bool(geofence_ok is True and (nfz.get("ok") is True or nfz.get("ok") is None)),
            "geofence_ok": geofence_ok is True,
            "bounds_ok": geofence_ok is True,
            "airspace_segment": payload.airspace_segment,
            "no_fly_zone": dict(nfz) if isinstance(nfz, dict) else None,
        }
        SIM.set_geofence_result(payload.uav_id, session_geofence)
        _save_uav_utm_session(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            utm_approval=utm_verify,
            utm_geofence_result=session_geofence,
        )
        _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        sim_now = SIM.status(payload.uav_id)
        mission_save = _save_verified_mission_and_paths(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            route_id=str(sim_now.get("route_id", after_route_id) or after_route_id),
            waypoints=[dict(w) for w in sim_now.get("waypoints", [])] if isinstance(sim_now.get("waypoints"), list) else after_waypoints,
            approval=utm_verify,
            source="agent_chat_auto_verify",
            copy_agent_if_missing=False,
        )
        if isinstance(mission_save, dict):
            agent_result["mission"] = mission_save.get("mission")
            agent_result["approved_route_records"] = mission_save.get("approved_route_records")

    workflow = run_copilot_workflow(
        {
            "uav_id": payload.uav_id,
            "airspace_segment": payload.airspace_segment,
            "prompt": prompt,
            "route_id": route_id,
            "effective_waypoints": effective_waypoints,
            "optimization_profile": payload.optimization_profile,
            "auto_verify": payload.auto_verify,
            "auto_network_optimize": payload.auto_network_optimize,
            "network_mode": payload.network_mode,
        }
    )
    if workflow.get("status") != "success" or not isinstance(workflow.get("result"), dict):
        fallback = _run_uav_agent_chat_heuristic(payload)
        if fallback.get("status") == "success" and isinstance(fallback.get("result"), dict):
            _apply_agent_chat_side_effects(fallback["result"])
            fallback["session"] = _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        return fallback
    agent_result = workflow["result"]
    if isinstance(agent_result, dict):
        _apply_agent_chat_side_effects(agent_result)
    sync = _log_uav_action("agent_chat", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=agent_result, entity_id=payload.uav_id)
    return {
        "status": "success",
        "sync": sync,
        "session": _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id),
        "result": agent_result,
    }


@router.post("/api/uav/live/geofence-submit")
@router.post("/api/uav/sim/geofence-submit")
def post_geofence_submit(uav_id: str = "uav-1", airspace_segment: str = "sector-A3", user_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    utm_mirror_sync = _refresh_utm_mirror_from_real_service(airspace_segment=airspace_segment)
    invoke_payload = {"uav_id": uav_id, "airspace_segment": airspace_segment}
    log_payload = {**invoke_payload, "user_id": resolved_user_id}
    result = uav_submit_route_to_utm_geofence_check.invoke(invoke_payload)
    if isinstance(result, dict):
        result_obj = result.get("result") if isinstance(result.get("result"), dict) else {}
        geofence = result_obj.get("geofence") if isinstance(result_obj, dict) and isinstance(result_obj.get("geofence"), dict) else None
        if isinstance(geofence, dict):
            _save_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id, utm_geofence_result=geofence)
            _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=uav_id)
    sim_now = SIM.status(uav_id)
    lifecycle_route_id = str(sim_now.get("route_id", "route-1") or "route-1")
    lifecycle_waypoints = [dict(w) for w in sim_now.get("waypoints", []) if isinstance(w, dict)] if isinstance(sim_now.get("waypoints"), list) else []
    dss_intent_result = _upsert_local_dss_intent_for_uav(
        user_id=resolved_user_id,
        uav_id=uav_id,
        route_id=lifecycle_route_id,
        waypoints=lifecycle_waypoints,
        airspace_segment=airspace_segment,
        state="accepted",
        conflict_policy="conditional_approve",
        source="geofence_submit",
        lifecycle_phase="submitted",
    )
    _save_uav_utm_session(
        user_id=resolved_user_id,
        uav_id=uav_id,
        utm_dss_result=dss_intent_result if isinstance(dss_intent_result, dict) else None,
    )
    sync = _log_uav_action("geofence_submit", payload=log_payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
        result["session"] = _get_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id)
        result["utmMirrorSync"] = utm_mirror_sync
        result["dss_intent_result"] = dss_intent_result
    return result


@router.post("/api/uav/live/request-approval")
@router.post("/api/uav/sim/request-approval")
def post_request_approval(payload: ApprovalPayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    route_id_for_dss, _route_waypoints_for_check = _sim_waypoints(payload.uav_id)
    _enforce_uav_capability_limits_or_raise(
        uav_id=payload.uav_id,
        requested_speed_mps=payload.requested_speed_mps,
        waypoints=_route_waypoints_for_check,
        context="uav_request_approval",
    )
    start_at, end_at = _default_time_window()
    effective_start_at = payload.planned_start_at or start_at
    effective_end_at = payload.planned_end_at or end_at
    dss_intent_result = _upsert_local_dss_intent_for_uav(
        user_id=resolved_user_id,
        uav_id=payload.uav_id,
        route_id=route_id_for_dss,
        waypoints=[dict(w) for w in _route_waypoints_for_check if isinstance(w, dict)],
        airspace_segment=payload.airspace_segment,
        state="contingent",
        conflict_policy=payload.dss_conflict_policy,
        source="request_approval",
        lifecycle_phase="approval_requested",
        planned_start_at=effective_start_at,
        planned_end_at=effective_end_at,
    )
    _save_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id, utm_dss_result=dss_intent_result if isinstance(dss_intent_result, dict) else None)
    blocking_conflicts = (
        dss_intent_result.get("blocking_conflicts")
        if isinstance(dss_intent_result, dict) and isinstance(dss_intent_result.get("blocking_conflicts"), list)
        else []
    )
    dss_status = str(dss_intent_result.get("status", "")).strip().lower() if isinstance(dss_intent_result, dict) else ""
    dss_error = str(dss_intent_result.get("error", "")).strip() if isinstance(dss_intent_result, dict) else ""
    if isinstance(dss_intent_result, dict) and (dss_status == "error" or dss_error):
        result = {
            "status": "error",
            "error": "dss_unavailable",
            "message": "Approval blocked because DSS intent publication failed",
            "dss_intent_result": dss_intent_result,
        }
        sync = _log_uav_action(
            "request_approval_blocked_dss_error",
            payload={**payload.model_dump(), "user_id": resolved_user_id, "route_id": route_id_for_dss},
            result={"status": dss_status, "error": dss_error},
            entity_id=payload.uav_id,
        )
        return {
            "status": "error",
            "sync": sync,
            "session": _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id),
            "result": result,
        }
    if isinstance(dss_intent_result, dict) and (
        dss_intent_result.get("status") == "rejected" or len(blocking_conflicts) > 0
    ):
        result = {
            "status": "error",
            "error": "dss_strategic_conflict",
            "message": "Approval blocked by DSS strategic conflict policy",
            "dss_intent_result": dss_intent_result,
        }
        sync = _log_uav_action(
            "request_approval_blocked_dss_conflict",
            payload={**payload.model_dump(), "user_id": resolved_user_id, "route_id": route_id_for_dss},
            result={"blocking_conflicts": len(blocking_conflicts), "status": dss_intent_result.get("status")},
            entity_id=payload.uav_id,
        )
        return {
            "status": "error",
            "sync": sync,
            "session": _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id),
            "result": result,
        }
    utm_mirror_sync = _refresh_utm_mirror_from_real_service(
        airspace_segment=payload.airspace_segment,
        operator_license_id=payload.operator_license_id,
    )
    req = {
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "operator_license_id": payload.operator_license_id,
        "required_license_class": payload.required_license_class,
        "requested_speed_mps": payload.requested_speed_mps,
        "planned_start_at": effective_start_at,
        "planned_end_at": effective_end_at,
    }
    result = uav_request_utm_approval.invoke(req)
    mission_save = None
    if isinstance(result, dict):
        result_obj = result.get("result") if isinstance(result.get("result"), dict) else {}
        approval = result_obj.get("approval") if isinstance(result_obj, dict) and isinstance(result_obj.get("approval"), dict) else None
        uav_obj = result_obj.get("uav") if isinstance(result_obj, dict) and isinstance(result_obj.get("uav"), dict) else {}
        geofence = uav_obj.get("utm_geofence_result") if isinstance(uav_obj, dict) and isinstance(uav_obj.get("utm_geofence_result"), dict) else None
        if isinstance(approval, dict) or isinstance(geofence, dict):
            _save_uav_utm_session(
                user_id=resolved_user_id,
                uav_id=payload.uav_id,
                utm_approval=approval if isinstance(approval, dict) else None,
                utm_geofence_result=geofence if isinstance(geofence, dict) else None,
                utm_dss_result=dss_intent_result if isinstance(dss_intent_result, dict) else None,
            )
            _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        if isinstance(approval, dict):
            waypoints = [dict(w) for w in uav_obj.get("waypoints", [])] if isinstance(uav_obj, dict) and isinstance(uav_obj.get("waypoints"), list) else []
            route_id = str(uav_obj.get("route_id", "route-1")) if isinstance(uav_obj, dict) else "route-1"
            mission_save = _save_verified_mission_and_paths(
                user_id=resolved_user_id,
                uav_id=payload.uav_id,
                route_id=route_id,
                waypoints=waypoints,
                approval=approval,
                source="request_approval",
                planned_start_at=req.get("planned_start_at"),
                planned_end_at=req.get("planned_end_at"),
                copy_agent_if_missing=False,
            )
    sync = _log_uav_action("request_approval", payload={**req, "user_id": resolved_user_id}, result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
        result["session"] = _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        result["utmMirrorSync"] = utm_mirror_sync
        result["dss_intent_result"] = dss_intent_result
        if mission_save is not None:
            result["mission"] = mission_save.get("mission")
            result["approved_route_records"] = mission_save.get("approved_route_records")
    return result


@router.post("/api/uav/live/utm-submit-mission")
@router.post("/api/uav/sim/utm-submit-mission")
def post_utm_submit_mission(payload: ApprovalPayload) -> Dict[str, Any]:
    """Backend-orchestrated UTM workflow: route checks -> geofence submit -> verify -> approval.

    This keeps session/DB updates and UTM actions on the backend instead of relying on frontend step ordering.
    """
    from .api_routes_utm import route_checks, verify_from_uav

    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    sim_before_submit = SIM.status_if_exists(payload.uav_id)
    submit_gate: Dict[str, Any] = {
        "battery_ok": True,
        "battery_pct": None,
        "issues": [],
    }
    if isinstance(sim_before_submit, dict):
        battery = sim_before_submit.get("battery_pct")
        if isinstance(battery, (int, float)):
            submit_gate["battery_pct"] = round(float(battery), 2)
            if float(battery) < 15.0:
                submit_gate["battery_ok"] = False
                submit_gate["issues"] = [f"Battery low ({float(battery):.0f}%)"]
    utm_mirror_sync = _refresh_utm_mirror_from_real_service(
        airspace_segment=payload.airspace_segment,
        operator_license_id=payload.operator_license_id,
    )
    if submit_gate.get("battery_ok") is True:
        route_checks_payload = RouteCheckPayload(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            airspace_segment=payload.airspace_segment,
            route_id=None,
            requested_speed_mps=payload.requested_speed_mps,
            operator_license_id=payload.operator_license_id,
        )
        route_checks_result = route_checks(route_checks_payload)
        geofence_result = post_geofence_submit(
            uav_id=payload.uav_id,
            airspace_segment=payload.airspace_segment,
            user_id=resolved_user_id,
        )
        verify_payload = VerifyFromUavPayload(
            **{
                **payload.model_dump(),
                "user_id": resolved_user_id,
            }
        )
        verify_result = verify_from_uav(verify_payload)
    else:
        route_checks_result = {
            "status": "skipped",
            "reason": "battery_low",
            "result": {"issues": list(submit_gate.get("issues", []))},
        }
        geofence_result = {
            "status": "skipped",
            "reason": "battery_low",
            "result": {"issues": list(submit_gate.get("issues", []))},
        }
        verify_result = {
            "status": "skipped",
            "reason": "battery_low",
            "result": {
                "approved": False,
                "decision": {
                    "status": "rejected",
                    "reasons": list(submit_gate.get("issues", [])),
                    "messages": ["Submit-time gate failed before UTM verify"],
                },
            },
        }

    verify_ok = False
    if isinstance(verify_result, dict):
        vr = verify_result.get("result")
        if isinstance(vr, dict):
            verify_ok = vr.get("approved") is True

    if verify_ok:
        approval_payload = ApprovalPayload(**{**payload.model_dump(), "user_id": resolved_user_id})
        approval_result = post_request_approval(approval_payload)
    else:
        skipped_reason = "verify_not_approved" if submit_gate.get("battery_ok") is True else "battery_low"
        approval_result = {
            "status": "skipped",
            "reason": skipped_reason,
            "issues": list(submit_gate.get("issues", [])),
            "session": _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id),
        }

    approval_obj = None
    if isinstance(approval_result, dict):
        ar = approval_result.get("result")
        if isinstance(ar, dict):
            inner = ar.get("result") if isinstance(ar.get("result"), dict) else None
            if isinstance(inner, dict) and isinstance(inner.get("approval"), dict):
                approval_obj = inner.get("approval")
            elif isinstance(ar.get("approval"), dict):
                approval_obj = ar.get("approval")

    session_now = _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id)
    aggregate = {
        "workflow": "utm_submit_mission_auto",
        "user_id": resolved_user_id,
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "utm_mirror_sync": utm_mirror_sync,
        "route_checks": route_checks_result,
        "geofence_submit": geofence_result,
        "verify_from_uav": verify_result,
        "approval_request": approval_result,
        "submit_gate": submit_gate,
        "approved": bool(isinstance(approval_obj, dict) and approval_obj.get("approved") is True),
        "session": session_now,
    }
    sync = _log_uav_action(
        "utm_submit_mission_auto",
        payload={**payload.model_dump(), "user_id": resolved_user_id},
        result={
            "approved": aggregate["approved"],
            "uav_id": payload.uav_id,
            "user_id": resolved_user_id,
            "airspace_segment": payload.airspace_segment,
        },
        entity_id=payload.uav_id,
    )
    return {"status": "success", "sync": sync, "result": aggregate}


@router.post("/api/uav/live/launch")
@router.post("/api/uav/sim/launch")
def post_launch(uav_id: str = "uav-1", user_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=uav_id)
    pre = SIM.status(uav_id)
    session = _get_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id)
    approval = session.get("utm_approval") if isinstance(session.get("utm_approval"), dict) else {}
    mission_defaults = _get_uav_mission_defaults(user_id=resolved_user_id, uav_id=uav_id)
    airspace_for_dss = str(approval.get("airspace_segment") or mission_defaults.get("airspace_segment") or "sector-A3")
    route_id_for_dss = str(pre.get("route_id", "route-1") or "route-1")
    waypoints_for_dss = [dict(w) for w in pre.get("waypoints", []) if isinstance(w, dict)] if isinstance(pre.get("waypoints"), list) else []
    pre_armed = bool(pre.get("armed"))
    pre_active = bool(pre.get("active"))
    pre_wp_index = int(pre.get("waypoint_index", 0) or 0)
    pre_wp_total = int(pre.get("waypoints_total", 0) or 0)
    mission_complete_armed = bool(pre_armed and (not pre_active) and pre_wp_total > 0 and pre_wp_index >= (pre_wp_total - 1))
    if pre_armed and not mission_complete_armed:
        dss_intent_result = _upsert_local_dss_intent_for_uav(
            user_id=resolved_user_id,
            uav_id=uav_id,
            route_id=route_id_for_dss,
            waypoints=waypoints_for_dss,
            airspace_segment=airspace_for_dss,
            state="activated",
            conflict_policy="conditional_approve",
            source="launch",
            lifecycle_phase="launched",
            metadata_extra={"launch_skipped": True},
        )
        _save_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id, utm_dss_result=dss_intent_result)
        warning = "UAV already launched. No need to launch again."
        result = {
            "status": "warning",
            "agent": "uav",
            "tool": "uav_launch",
            "warning": warning,
            "result": pre,
            "dss_intent_result": dss_intent_result,
        }
        sync = _log_uav_action(
            "launch_skipped_already_launched",
            payload={"uav_id": uav_id, "user_id": resolved_user_id, "reason": "already_launched"},
            result={"warning": warning, "flight_phase": pre.get("flight_phase"), "active": pre.get("active"), "armed": pre.get("armed")},
            entity_id=uav_id,
        )
        result["sync"] = sync
        return result
    if mission_complete_armed:
        _log_uav_action(
            "launch_restart_from_mission_complete",
            payload={"uav_id": uav_id, "user_id": resolved_user_id},
            result={
                "status": "restart",
                "reason": "mission_complete_armed",
                "waypoint_index": pre_wp_index,
                "waypoints_total": pre_wp_total,
            },
            entity_id=uav_id,
        )
    _enforce_flight_control_gate_or_raise(uav_id=uav_id, action="launch", user_id=resolved_user_id)
    invoke_payload = {"uav_id": uav_id, "require_utm_approval": True}
    log_payload = {**invoke_payload, "user_id": resolved_user_id}
    result = uav_launch.invoke(invoke_payload)
    if isinstance(result, dict) and str(result.get("status", "")).strip().lower() == "error":
        detail = {
            "status": "error",
            "error": "launch_failed",
            "uav_id": uav_id,
            "user_id": resolved_user_id,
            "tool_result": result,
        }
        sync = _log_uav_action("launch_failed", payload=log_payload, result=detail, entity_id=uav_id)
        detail["sync"] = sync
        raise HTTPException(status_code=409, detail=detail)
    if isinstance(result, dict) and result.get("status") == "success":
        _touch_mission_execution(user_id=resolved_user_id, uav_id=uav_id, action="launch")
        post = SIM.status(uav_id)
        route_id_after = str(post.get("route_id", route_id_for_dss) or route_id_for_dss)
        waypoints_after = [dict(w) for w in post.get("waypoints", []) if isinstance(w, dict)] if isinstance(post.get("waypoints"), list) else waypoints_for_dss
        dss_intent_result = _upsert_local_dss_intent_for_uav(
            user_id=resolved_user_id,
            uav_id=uav_id,
            route_id=route_id_after,
            waypoints=waypoints_after,
            airspace_segment=airspace_for_dss,
            state="activated",
            conflict_policy="conditional_approve",
            source="launch",
            lifecycle_phase="launched",
            metadata_extra={"launched": True},
        )
        _save_uav_utm_session(user_id=resolved_user_id, uav_id=uav_id, utm_dss_result=dss_intent_result)
        result["dss_intent_result"] = dss_intent_result
    sync = _log_uav_action("launch", payload=log_payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/uav/live/step")
@router.post("/api/uav/sim/step")
def post_step(payload: StepPayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
    _enforce_flight_control_gate_or_raise(uav_id=payload.uav_id, action="step", user_id=resolved_user_id)
    result = uav_sim_step.invoke({"uav_id": payload.uav_id, "ticks": payload.ticks})
    if isinstance(result, dict) and result.get("status") == "success":
        _touch_mission_execution(user_id=resolved_user_id, uav_id=payload.uav_id, action="step")
    sync = _log_uav_action("step", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/uav/live/mission-action")
@router.post("/api/uav/sim/mission-action")
def post_mission_action(payload: MissionActionPayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
    snap = SIM.status(payload.uav_id)
    action_raw = str(payload.action or "").strip().lower().replace(" ", "_")
    normalized = _MISSION_ACTION_ALIASES.get(action_raw, action_raw)
    if normalized not in {"photo", "temperature", "inspect", "hover"}:
        return {
            "status": "error",
            "error": "invalid_mission_action",
            "result": {
                "allowed_actions": ["photo", "measure", "temperature", "inspect", "hover"],
                "received": payload.action,
            },
        }
    waypoint_index = int(snap.get("waypoint_index", 0) or 0)
    waypoints = snap.get("waypoints") if isinstance(snap.get("waypoints"), list) else []
    waypoint_row = waypoints[waypoint_index] if isinstance(waypoints, list) and 0 <= waypoint_index < len(waypoints) else {}
    waypoint_action = str(waypoint_row.get("action", "transit")) if isinstance(waypoint_row, dict) else "transit"
    result = {
        "status": "success",
        "uav_id": payload.uav_id,
        "user_id": resolved_user_id,
        "action": normalized,
        "note": str(payload.note or ""),
        "armed": bool(snap.get("armed")),
        "active": bool(snap.get("active")),
        "flight_phase": str(snap.get("flight_phase", "")),
        "waypoint_index": waypoint_index,
        "waypoint_action": waypoint_action,
        "route_progress_pct": float(snap.get("route_progress_pct", 0.0) or 0.0),
    }
    _touch_mission_execution(user_id=resolved_user_id, uav_id=payload.uav_id, action=f"mission_action_{normalized}")
    sync = _log_uav_action(
        f"mission_action_{normalized}",
        payload={**payload.model_dump(), "user_id": resolved_user_id, "normalized_action": normalized},
        result=result,
        entity_id=payload.uav_id,
    )
    result["sync"] = sync
    return result


@router.post("/api/uav/live/hold")
@router.post("/api/uav/sim/hold")
def post_hold(payload: HoldPayload) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
    _enforce_flight_control_gate_or_raise(uav_id=payload.uav_id, action="hold", user_id=resolved_user_id)
    result = uav_hold.invoke({"uav_id": payload.uav_id, "reason": payload.reason})
    if isinstance(result, dict) and result.get("status") == "success":
        _touch_mission_execution(user_id=resolved_user_id, uav_id=payload.uav_id, action="hold")
    sync = _log_uav_action("hold", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/uav/live/resume")
@router.post("/api/uav/sim/resume")
def post_resume(uav_id: str = "uav-1", user_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=uav_id)
    _enforce_flight_control_gate_or_raise(uav_id=uav_id, action="resume", user_id=resolved_user_id)
    invoke_payload = {"uav_id": uav_id}
    log_payload = {**invoke_payload, "user_id": resolved_user_id}
    result = uav_resume.invoke(invoke_payload)
    if isinstance(result, dict) and result.get("status") == "success":
        _touch_mission_execution(user_id=resolved_user_id, uav_id=uav_id, action="resume")
    sync = _log_uav_action("resume", payload=log_payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/uav/live/rth")
@router.post("/api/uav/sim/rth")
def post_return_to_home(uav_id: str = "uav-1", user_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=uav_id)
    _enforce_flight_control_gate_or_raise(uav_id=uav_id, action="rth", user_id=resolved_user_id)
    invoke_payload = {"uav_id": uav_id}
    log_payload = {**invoke_payload, "user_id": resolved_user_id}
    result = uav_return_to_home.invoke(invoke_payload)
    if isinstance(result, dict) and result.get("status") == "success":
        _touch_mission_execution(user_id=resolved_user_id, uav_id=uav_id, action="rth")
    sync = _log_uav_action("rth", payload=log_payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/uav/live/land")
@router.post("/api/uav/sim/land")
def post_land(uav_id: str = "uav-1", user_id: Optional[str] = None) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=uav_id)
    _enforce_flight_control_gate_or_raise(uav_id=uav_id, action="land", user_id=resolved_user_id)
    invoke_payload = {"uav_id": uav_id}
    log_payload = {**invoke_payload, "user_id": resolved_user_id}
    result = uav_land.invoke(invoke_payload)
    if isinstance(result, dict) and result.get("status") == "success":
        _touch_mission_execution(user_id=resolved_user_id, uav_id=uav_id, action="land")
    sync = _log_uav_action("land", payload=log_payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/uav/live/end-mission")
@router.post("/api/uav/sim/end-mission")
def post_end_mission(
    uav_id: str = "uav-1",
    user_id: Optional[str] = None,
    cleanup_stale: bool = True,
    stale_window_minutes: int = 15,
) -> Dict[str, Any]:
    resolved_user_id = _resolve_session_user_id(uav_id=uav_id, user_id=user_id)
    window_minutes = max(1, min(240, int(stale_window_minutes)))
    out = _end_mission_cleanup_for_scope(
        user_id=resolved_user_id,
        uav_id=uav_id,
        cleanup_stale=bool(cleanup_stale),
        stale_window_minutes=window_minutes,
    )
    if str(out.get("status", "")).strip().lower() != "success":
        return {
            "status": "error",
            "error": out.get("error") or "end_mission_failed",
            "result": out,
        }
    sync = _log_uav_action(
        "end_mission_cleanup",
        payload={
            "uav_id": uav_id,
            "user_id": resolved_user_id,
            "cleanup_stale": bool(cleanup_stale),
            "stale_window_minutes": window_minutes,
        },
        result={
            "forced_land": bool(out.get("forced_land")),
            "post_flight_phase": out.get("post_flight_phase"),
            "cleanup": out.get("cleanup"),
        },
        entity_id=uav_id,
    )
    return {"status": "success", "sync": sync, "result": out}
