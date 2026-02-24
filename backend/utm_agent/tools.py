from langchain.tools import tool

from .service import UTM_SERVICE


@tool
def utm_verify_flight_plan(
    uav_id: str = "uav-1",
    airspace_segment: str = "sector-A3",
    expires_at: str = "2099-01-01T00:00:00Z",
    route_id: str = "route-1",
    waypoints: list[dict] | None = None,
    requested_speed_mps: float = 12.0,
    planned_start_at: str = "",
    planned_end_at: str = "",
    operator_license_id: str = "op-001",
    required_license_class: str = "VLOS",
) -> dict:
    """Verify a UAV flight plan against UTM policy (weather/NFZ/regulations)."""
    rec = UTM_SERVICE.verify_flight_plan(
        uav_id=uav_id,
        airspace_segment=airspace_segment,
        route_id=route_id,
        waypoints=waypoints,
        requested_speed_mps=requested_speed_mps,
        planned_start_at=planned_start_at or None,
        planned_end_at=planned_end_at or None,
        operator_license_id=operator_license_id or None,
        required_license_class=required_license_class,
    )
    return {
        "status": "success",
        "agent": "utm",
        "result": rec,
    }


@tool
def utm_reserve_corridor(uav_id: str = "uav-1", airspace_segment: str = "sector-A3") -> dict:
    """Reserve an airspace corridor (stub)."""
    return {"status": "success", "agent": "utm", "result": {"uav_id": uav_id, "airspace_segment": airspace_segment, "reserved": True}}


@tool
def utm_check_geofence(
    uav_id: str = "uav-1",
    route_id: str = "route-1",
    airspace_segment: str = "sector-A3",
    waypoints: list[dict] | None = None,
) -> dict:
    """Check basic geofence compliance for a route against simulated airspace bounds and no-fly zones."""
    pts = waypoints or []
    bounds = {"sector-A3": {"x": [0, 400], "y": [0, 300], "z": [0, 120]}}
    seg = bounds.get(airspace_segment, {"x": [-1e9, 1e9], "y": [-1e9, 1e9], "z": [0, 120]})
    out_of_bounds = []
    for i, wp in enumerate(pts):
        x = float(wp.get("x", 0.0))
        y = float(wp.get("y", 0.0))
        z = float(wp.get("z", 0.0))
        if not (seg["x"][0] <= x <= seg["x"][1] and seg["y"][0] <= y <= seg["y"][1] and seg["z"][0] <= z <= seg["z"][1]):
            out_of_bounds.append({"index": i, "wp": {"x": x, "y": y, "z": z}})
    nfz = UTM_SERVICE.check_no_fly_zones(pts)
    geofence_ok = len(out_of_bounds) == 0 and nfz["ok"]
    return {
        "status": "success",
        "agent": "utm",
        "result": {
            "uav_id": uav_id,
            "route_id": route_id,
            "airspace_segment": airspace_segment,
            "geofence_ok": geofence_ok,
            "out_of_bounds": out_of_bounds,
            "no_fly_zone": nfz,
        },
    }


@tool
def utm_weather_check(airspace_segment: str = "sector-A3") -> dict:
    """Check simulated weather constraints for an airspace segment."""
    return {"status": "success", "agent": "utm", "result": UTM_SERVICE.check_weather(airspace_segment)}


@tool
def utm_no_fly_zone_check(route_id: str = "route-1", waypoints: list[dict] | None = None) -> dict:
    """Check a route against simulated no-fly zones."""
    return {"status": "success", "agent": "utm", "result": {"route_id": route_id, **UTM_SERVICE.check_no_fly_zones(waypoints or [])}}


@tool
def utm_regulation_check(route_id: str = "route-1", waypoints: list[dict] | None = None, requested_speed_mps: float = 12.0) -> dict:
    """Check route geometry/altitude/speed against simulated UTM regulations."""
    return {
        "status": "success",
        "agent": "utm",
        "result": {"route_id": route_id, **UTM_SERVICE.check_regulations(waypoints or [], requested_speed_mps=requested_speed_mps)},
    }


@tool
def utm_time_window_check(planned_start_at: str = "", planned_end_at: str = "") -> dict:
    """Check mission time-window validity against simulated UTM regulations."""
    return {
        "status": "success",
        "agent": "utm",
        "result": UTM_SERVICE.check_time_window(planned_start_at=planned_start_at or None, planned_end_at=planned_end_at or None),
    }


@tool
def utm_operator_license_check(operator_license_id: str = "op-001", required_license_class: str = "VLOS") -> dict:
    """Check operator license validity/class against simulated UTM policy."""
    return {
        "status": "success",
        "agent": "utm",
        "result": UTM_SERVICE.check_operator_license(operator_license_id=operator_license_id, required_class=required_license_class),
    }


@tool
def utm_register_operator_license(
    operator_license_id: str,
    license_class: str = "VLOS",
    expires_at: str = "2099-01-01T00:00:00Z",
    active: bool = True,
) -> dict:
    """Register/update a simulated operator license for testing."""
    return {
        "status": "success",
        "agent": "utm",
        "result": UTM_SERVICE.register_operator_license(
            operator_license_id=operator_license_id,
            license_class=license_class,
            expires_at=expires_at,
            active=active,
        ),
    }


@tool
def utm_set_weather(
    airspace_segment: str = "sector-A3",
    wind_mps: float = 8.0,
    visibility_km: float = 10.0,
    precip_mmph: float = 0.0,
    storm_alert: bool = False,
) -> dict:
    """Update simulated weather for testing approvals/denials."""
    rec = UTM_SERVICE.set_weather(
        airspace_segment,
        wind_mps=float(wind_mps),
        visibility_km=float(visibility_km),
        precip_mmph=float(precip_mmph),
        storm_alert=bool(storm_alert),
    )
    return {"status": "success", "agent": "utm", "result": {"airspace_segment": airspace_segment, "weather": rec}}


TOOLS = [
    utm_verify_flight_plan,
    utm_reserve_corridor,
    utm_check_geofence,
    utm_weather_check,
    utm_no_fly_zone_check,
    utm_regulation_check,
    utm_time_window_check,
    utm_operator_license_check,
    utm_register_operator_license,
    utm_set_weather,
]
