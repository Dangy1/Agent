"""UTM simulator API (separate process/port)."""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError("utm_agent.api requires fastapi and pydantic") from e

from uav_agent.simulator import SIM
from utm_agent.service import UTM_SERVICE
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


class CorridorPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"


class RouteCheckPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    requested_speed_mps: float = 12.0
    route_id: Optional[str] = None


class TimeWindowCheckPayload(BaseModel):
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


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
    return {
        "uav_id": uav_id,
        "route_id": route_id,
        "airspace_segment": airspace_segment,
        "geofence_ok": len(out_of_bounds) == 0 and nfz.get("ok", False),
        "out_of_bounds": out_of_bounds,
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


@app.get("/api/utm/state")
def get_utm_state(airspace_segment: str = "sector-A3") -> Dict[str, Any]:
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "airspace_segment": airspace_segment,
            "weather": UTM_SERVICE.get_weather(airspace_segment),
            "weatherChecks": UTM_SERVICE.check_weather(airspace_segment),
            "noFlyZones": list(UTM_SERVICE.no_fly_zones),
            "regulations": dict(UTM_SERVICE.regulations),
            "licenses": dict(UTM_SERVICE.operator_licenses),
        },
    }


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
    result = {"uav_id": payload.uav_id, "airspace_segment": payload.airspace_segment, "reserved": True}
    sync = _log_utm_action("reserve_corridor", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/checks/route")
def route_checks(payload: RouteCheckPayload) -> Dict[str, Any]:
    route_id, waypoints = _sim_waypoints(payload.uav_id)
    rid = payload.route_id or route_id
    geofence = _geofence_check_from_waypoints(
        uav_id=payload.uav_id,
        route_id=rid,
        airspace_segment=payload.airspace_segment,
        waypoints=waypoints,
    )
    result = {
        "uav_id": payload.uav_id,
        "route_id": rid,
        "airspace_segment": payload.airspace_segment,
        "waypoints_total": len(waypoints),
        "geofence": geofence,
        "no_fly_zone": UTM_SERVICE.check_no_fly_zones(waypoints),
        "regulations": UTM_SERVICE.check_regulations(waypoints, requested_speed_mps=payload.requested_speed_mps),
    }
    sync = _log_utm_action("route_checks", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {
        "status": "success",
        "sync": sync,
        "result": result,
    }


@app.post("/api/utm/checks/time-window")
def check_time_window(payload: TimeWindowCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_time_window(payload.planned_start_at, payload.planned_end_at)
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
    route_id, waypoints = _sim_waypoints(payload.uav_id)
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
    sync = _log_utm_action("verify_from_uav", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}
