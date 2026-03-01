"""UTM-domain API routes (weather, NFZ, licensing, verification)."""

from __future__ import annotations

from fastapi import APIRouter

from .api_shared import *  # noqa: F401,F403

router = APIRouter()


@router.get("/api/utm/weather")
def get_weather(airspace_segment: str = "sector-A3") -> Dict[str, Any]:
    return {"status": "success", "result": UTM_SERVICE.check_weather(airspace_segment)}


@router.post("/api/utm/weather")
def set_weather(payload: WeatherPayload) -> Dict[str, Any]:
    weather = UTM_SERVICE.set_weather(
        payload.airspace_segment,
        wind_mps=payload.wind_mps,
        visibility_km=payload.visibility_km,
        precip_mmph=payload.precip_mmph,
        storm_alert=payload.storm_alert,
    )
    result = {"airspace_segment": payload.airspace_segment, "weather": weather}
    sync = _log_uav_action("utm_set_weather", payload=payload.model_dump(), result=result, entity_id=payload.airspace_segment)
    return {"status": "success", "sync": sync, "result": result}


@router.get("/api/utm/nfz")
def list_no_fly_zones() -> Dict[str, Any]:
    return {"status": "success", "result": {"no_fly_zones": UTM_SERVICE.no_fly_zones}}


@router.post("/api/utm/nfz")
def add_no_fly_zone(payload: NoFlyZonePayload) -> Dict[str, Any]:
    result = UTM_SERVICE.add_no_fly_zone(**payload.model_dump())
    sync = _log_uav_action("utm_add_nfz", payload=payload.model_dump(), result=result, entity_id=str(result.get("zone_id", "")))
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/utm/license")
def register_license(payload: LicensePayload) -> Dict[str, Any]:
    result = UTM_SERVICE.register_operator_license(**payload.model_dump())
    sync = _log_uav_action("utm_register_license", payload=payload.model_dump(), result=result, entity_id=payload.operator_license_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/utm/corridor/reserve")
def reserve_corridor(payload: CorridorPayload) -> Dict[str, Any]:
    result = {"uav_id": payload.uav_id, "airspace_segment": payload.airspace_segment, "reserved": True}
    sync = _log_uav_action("utm_reserve_corridor", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/utm/checks/route")
def route_checks(payload: RouteCheckPayload) -> Dict[str, Any]:
    utm_mirror_sync = _refresh_utm_mirror_from_real_service(
        airspace_segment=payload.airspace_segment,
        operator_license_id=payload.operator_license_id,
    )
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    route_id, waypoints = _sim_waypoints(payload.uav_id)
    _enforce_uav_capability_limits_or_raise(
        uav_id=payload.uav_id,
        requested_speed_mps=payload.requested_speed_mps,
        waypoints=waypoints,
        context="utm_route_checks",
    )
    rid = payload.route_id or route_id
    registry_row = _get_uav_registry_uav_row(payload.uav_id)
    effective_license_id = payload.operator_license_id or str(registry_row.get("operator_license_id", "op-001") or "op-001")
    geofence = _geofence_check_from_waypoints(
        uav_id=payload.uav_id,
        route_id=rid,
        airspace_segment=payload.airspace_segment,
        waypoints=waypoints,
    )
    result = {
        "user_id": resolved_user_id,
        "status": "success",
        "result": {
            "uav_id": payload.uav_id,
            "route_id": rid,
            "airspace_segment": payload.airspace_segment,
            "waypoints_total": len(waypoints),
            "proposed_context": {
                "user_id": resolved_user_id,
                "uav_id": payload.uav_id,
                "route_id": rid,
                "airspace_segment": payload.airspace_segment,
                "operator_license_id": effective_license_id,
                "owner_user_id": registry_row.get("owner_user_id"),
                "uav_registry": registry_row,
            },
            "geofence": geofence,
            "no_fly_zone": UTM_SERVICE.check_no_fly_zones(waypoints),
            "regulations": UTM_SERVICE.check_regulations(
                waypoints,
                requested_speed_mps=payload.requested_speed_mps,
                operator_license_id=effective_license_id,
            ),
            "effective_regulations": UTM_SERVICE.effective_regulations(effective_license_id),
        },
    }
    sync = _log_uav_action(
        "utm_check_route",
        payload={**payload.model_dump(), "user_id": resolved_user_id, "operator_license_id": effective_license_id},
        result=result.get("result"),
        entity_id=payload.uav_id,
    )
    result["sync"] = sync
    result["utmMirrorSync"] = utm_mirror_sync
    return result


@router.post("/api/utm/checks/time-window")
def check_time_window(payload: TimeWindowCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_time_window(payload.planned_start_at, payload.planned_end_at, payload.operator_license_id)
    sync = _log_uav_action("utm_check_time_window", payload=payload.model_dump(), result=result)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/utm/checks/license")
def check_license(payload: LicenseCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_operator_license(
        operator_license_id=payload.operator_license_id,
        required_class=payload.required_license_class,
    )
    sync = _log_uav_action("utm_check_license", payload=payload.model_dump(), result=result, entity_id=payload.operator_license_id)
    return {"status": "success", "sync": sync, "result": result}


@router.post("/api/utm/verify-from-uav")
def verify_from_uav(payload: VerifyFromUavPayload) -> Dict[str, Any]:
    utm_mirror_sync = _refresh_utm_mirror_from_real_service(
        airspace_segment=payload.airspace_segment,
        operator_license_id=payload.operator_license_id,
    )
    resolved_user_id = _resolve_session_user_id(uav_id=payload.uav_id, user_id=payload.user_id)
    route_id, waypoints = _sim_waypoints(payload.uav_id)
    _enforce_uav_capability_limits_or_raise(
        uav_id=payload.uav_id,
        requested_speed_mps=payload.requested_speed_mps,
        waypoints=waypoints,
        context="utm_verify_from_uav",
    )
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
    # Keep simulator state aligned with UTM-side verification so flight controls use the same approval/check data
    # shown in the UI.
    mission_save = None
    dss_intent_result = None
    if isinstance(result, dict):
        SIM.set_approval(payload.uav_id, result)
        checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
        session_geofence: Dict[str, Any] | None = None
        if isinstance(checks, dict):
            route_bounds = checks.get("route_bounds") if isinstance(checks.get("route_bounds"), dict) else {}
            nfz = checks.get("no_fly_zone") if isinstance(checks.get("no_fly_zone"), dict) else {}
            geofence_ok = route_bounds.get("ok")
            if geofence_ok is None:
                geofence_ok = route_bounds.get("geofence_ok")
            if geofence_ok is None:
                geofence_ok = route_bounds.get("bounds_ok")
            nfz_ok = nfz.get("ok")
            combined_ok = bool(geofence_ok is True and (nfz_ok is True or nfz_ok is None))
            session_geofence = {
                "ok": combined_ok,
                "geofence_ok": geofence_ok is True,
                "bounds_ok": geofence_ok is True,
                "airspace_segment": payload.airspace_segment,
                "no_fly_zone": nfz if isinstance(nfz, dict) else None,
            }
            SIM.set_geofence_result(payload.uav_id, session_geofence)
        _save_uav_utm_session(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            utm_approval=result,
            utm_geofence_result=session_geofence,
        )
        _sync_sim_utm_from_session(user_id=resolved_user_id, uav_id=payload.uav_id)
        mission_save = _save_verified_mission_and_paths(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            route_id=route_id,
            waypoints=[dict(w) for w in waypoints if isinstance(w, dict)],
            approval=result,
            source="utm_verify_from_uav",
            planned_start_at=payload.planned_start_at,
            planned_end_at=payload.planned_end_at,
            copy_agent_if_missing=False,
        )
        approved = bool(result.get("approved"))
        dss_intent_result = _upsert_local_dss_intent_for_uav(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            route_id=route_id,
            waypoints=[dict(w) for w in waypoints if isinstance(w, dict)],
            airspace_segment=payload.airspace_segment,
            state="contingent" if approved else "nonconforming",
            conflict_policy="conditional_approve",
            source="verify_from_uav",
            lifecycle_phase="verified" if approved else "verify_failed",
            metadata_extra={"approved": approved, "required_license_class": payload.required_license_class},
            planned_start_at=payload.planned_start_at,
            planned_end_at=payload.planned_end_at,
        )
        _save_uav_utm_session(
            user_id=resolved_user_id,
            uav_id=payload.uav_id,
            utm_dss_result=dss_intent_result if isinstance(dss_intent_result, dict) else None,
        )
    sync = _log_uav_action("utm_verify_from_uav", payload={**payload.model_dump(), "user_id": resolved_user_id}, result=result, entity_id=payload.uav_id)
    return {
        "status": "success",
        "sync": sync,
        "utmMirrorSync": utm_mirror_sync,
        "result": result,
        "session": _get_uav_utm_session(user_id=resolved_user_id, uav_id=payload.uav_id),
        "mission": mission_save.get("mission") if isinstance(mission_save, dict) else None,
        "approved_route_records": mission_save.get("approved_route_records") if isinstance(mission_save, dict) else None,
        "dss_intent_result": dss_intent_result,
    }
