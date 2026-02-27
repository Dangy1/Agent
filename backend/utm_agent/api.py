"""UTM API (supports simulator defaults and live-ingested data)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError("utm_agent.api requires fastapi and pydantic") from e

from uav_agent.simulator import SIM
from utm_agent.service import UTM_SERVICE
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
    metadata: Optional[Dict[str, Any]] = None


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
UTM_DATA_MODE = os.getenv("UTM_DATA_MODE", "auto").strip().lower() or "auto"


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


@app.get("/api/utm/state")
def get_utm_state(airspace_segment: str = "sector-A3", operator_license_id: Optional[str] = None) -> Dict[str, Any]:
    dss_intents = _get_dss_operational_intents()
    dss_subscriptions = _get_dss_subscriptions()
    dss_participants = _get_dss_participants()
    dss_notifications = _get_dss_notifications()
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "dataSource": _utm_source_info(),
        "result": {
            "airspace_segment": airspace_segment,
            "weather": UTM_SERVICE.get_weather(airspace_segment),
            "weatherChecks": UTM_SERVICE.check_weather(airspace_segment, operator_license_id=operator_license_id),
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
                "pendingNotificationCount": len([n for n in dss_notifications if str(n.get("status")) == "pending"]),
                "operationalIntents": list(dss_intents.values()),
                "subscriptions": list(dss_subscriptions.values()),
                "participants": list(dss_participants.values()),
            },
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
    intents = _get_dss_operational_intents()
    volume4d = _route_volume4d_from_waypoints(waypoints)
    upsert = dss_upsert_intent(
        intents,
        manager_uss_id="uss-local",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d=volume4d,
        metadata={
            "source": "reserve_corridor",
            "uav_id": payload.uav_id,
            "route_id": route_id,
            "airspace_segment": payload.airspace_segment,
        },
    )
    if upsert.get("stored"):
        _set_dss_operational_intents(intents)
    intent = upsert.get("intent") if isinstance(upsert.get("intent"), dict) else {}
    subscriptions = _get_dss_subscriptions()
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=intent.get("volume4d") if isinstance(intent.get("volume4d"), dict) else volume4d,
            event_type="create",
        )
        if upsert.get("stored")
        else []
    )
    queued_notifications = _queue_dss_notifications(
        notifications=notifications,
        event_type="create",
        source_intent_id=str(intent.get("intent_id") or ""),
    )
    result = {
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "route_id": route_id,
        "reserved": bool(upsert.get("stored")),
        "intent_result": upsert,
        "subscriptions_to_notify": notifications,
        "queued_notifications": queued_notifications,
    }
    sync = _log_utm_action("reserve_corridor", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/utm/dss/state")
def get_dss_state() -> Dict[str, Any]:
    intents = _get_dss_operational_intents()
    subscriptions = _get_dss_subscriptions()
    participants = _get_dss_participants()
    notifications = _get_dss_notifications()
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "operationalIntentCount": len(intents),
            "subscriptionCount": len(subscriptions),
            "participantCount": len(participants),
            "notificationCount": len(notifications),
            "pendingNotificationCount": len([n for n in notifications if str(n.get("status")) == "pending"]),
            "operationalIntents": list(intents.values()),
            "subscriptions": list(subscriptions.values()),
            "participants": list(participants.values()),
            "notifications": notifications[-50:],
        },
    }


@app.post("/api/utm/dss/operational-intents")
def post_operational_intent(payload: OperationalIntentPayload) -> Dict[str, Any]:
    intents = _get_dss_operational_intents()
    upsert = dss_upsert_intent(
        intents,
        intent_id=payload.intent_id,
        manager_uss_id=payload.manager_uss_id,
        state=payload.state,
        priority=payload.priority,
        conflict_policy=payload.conflict_policy,
        ovn=payload.ovn,
        uss_base_url=payload.uss_base_url,
        volume4d=payload.volume4d.as_volume4d(),
        metadata=payload.metadata,
    )
    if upsert.get("stored"):
        _set_dss_operational_intents(intents)

    intent = upsert.get("intent") if isinstance(upsert.get("intent"), dict) else {}
    event_type = "create" if int(intent.get("version", 1) or 1) <= 1 else "update"
    subscriptions = _get_dss_subscriptions()
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=intent.get("volume4d") if isinstance(intent.get("volume4d"), dict) else payload.volume4d.as_volume4d(),
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
        "upsert": upsert,
        "subscriptions_to_notify": notifications,
        "queued_notifications": queued_notifications,
        "counts": {"operationalIntents": len(intents), "subscriptions": len(subscriptions)},
    }
    sync = _log_utm_action(
        "dss_upsert_operational_intent",
        payload=payload.model_dump(),
        result={"status": upsert.get("status"), "stored": upsert.get("stored"), "intent_id": intent.get("intent_id")},
        entity_id=str(intent.get("intent_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/dss/operational-intents/query")
def post_query_operational_intents(payload: OperationalIntentQueryPayload) -> Dict[str, Any]:
    intents = _get_dss_operational_intents()
    items = dss_query_intents(
        intents,
        manager_uss_id=payload.manager_uss_id,
        states=payload.states,
        volume4d=payload.volume4d.as_volume4d() if isinstance(payload.volume4d, Volume4DPayload) else None,
    )
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "query": payload.model_dump(),
            "count": len(items),
            "items": items,
        },
    }


@app.get("/api/utm/dss/operational-intents/query")
def get_query_operational_intents(
    manager_uss_id: Optional[str] = None,
    states: Optional[str] = None,
) -> Dict[str, Any]:
    intents = _get_dss_operational_intents()
    parsed_states = [s.strip() for s in str(states or "").split(",") if s.strip()]
    items = dss_query_intents(
        intents,
        manager_uss_id=manager_uss_id,
        states=parsed_states or None,
        volume4d=None,
    )
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {
            "query": {"manager_uss_id": manager_uss_id, "states": parsed_states},
            "count": len(items),
            "items": items,
        },
    }


@app.delete("/api/utm/dss/operational-intents/{intent_id}")
def delete_operational_intent(intent_id: str) -> Dict[str, Any]:
    intents = _get_dss_operational_intents()
    before = intents.get(intent_id) if isinstance(intents.get(intent_id), dict) else None
    result = dss_delete_intent(intents, intent_id)
    if result.get("deleted"):
        _set_dss_operational_intents(intents)
    subscriptions = _get_dss_subscriptions()
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=before.get("volume4d") if isinstance(before, dict) and isinstance(before.get("volume4d"), dict) else {},
            event_type="delete",
        )
        if result.get("deleted")
        else []
    )
    queued_notifications = _queue_dss_notifications(
        notifications=notifications,
        event_type="delete",
        source_intent_id=intent_id,
    )
    sync = _log_utm_action(
        "dss_delete_operational_intent",
        payload={"intent_id": intent_id},
        result={"deleted": result.get("deleted"), "intent_id": intent_id},
        entity_id=intent_id,
    )
    return {
        "status": "success",
        "sync": sync,
        "result": {**result, "subscriptions_to_notify": notifications, "queued_notifications": queued_notifications},
    }


@app.post("/api/utm/dss/subscriptions")
def post_subscription(payload: SubscriptionPayload) -> Dict[str, Any]:
    subscriptions = _get_dss_subscriptions()
    result = dss_upsert_subscription(
        subscriptions,
        subscription_id=payload.subscription_id,
        manager_uss_id=payload.manager_uss_id,
        uss_base_url=payload.uss_base_url,
        callback_url=payload.callback_url,
        volume4d=payload.volume4d.as_volume4d(),
        notify_for=payload.notify_for,
        expires_at=payload.expires_at,
        metadata=payload.metadata,
    )
    _set_dss_subscriptions(subscriptions)
    sub = result.get("subscription") if isinstance(result.get("subscription"), dict) else {}
    sync = _log_utm_action(
        "dss_upsert_subscription",
        payload=payload.model_dump(),
        result={"subscription_id": sub.get("subscription_id"), "manager_uss_id": sub.get("manager_uss_id")},
        entity_id=str(sub.get("subscription_id") or ""),
    )
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/dss/subscriptions/query")
def post_query_subscriptions(payload: SubscriptionQueryPayload) -> Dict[str, Any]:
    subscriptions = _get_dss_subscriptions()
    items = dss_query_subscriptions(
        subscriptions,
        manager_uss_id=payload.manager_uss_id,
        volume4d=payload.volume4d.as_volume4d() if isinstance(payload.volume4d, Volume4DPayload) else None,
    )
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {"query": payload.model_dump(), "count": len(items), "items": items},
    }


@app.get("/api/utm/dss/subscriptions")
def get_subscriptions(manager_uss_id: Optional[str] = None) -> Dict[str, Any]:
    subscriptions = _get_dss_subscriptions()
    items = dss_query_subscriptions(subscriptions, manager_uss_id=manager_uss_id, volume4d=None)
    return {
        "status": "success",
        "sync": UTM_DB.get_sync(),
        "result": {"query": {"manager_uss_id": manager_uss_id}, "count": len(items), "items": items},
    }


@app.delete("/api/utm/dss/subscriptions/{subscription_id}")
def delete_subscription(subscription_id: str) -> Dict[str, Any]:
    subscriptions = _get_dss_subscriptions()
    result = dss_delete_subscription(subscriptions, subscription_id)
    if result.get("deleted"):
        _set_dss_subscriptions(subscriptions)
    sync = _log_utm_action(
        "dss_delete_subscription",
        payload={"subscription_id": subscription_id},
        result={"deleted": result.get("deleted"), "subscription_id": subscription_id},
        entity_id=subscription_id,
    )
    return {"status": "success", "sync": sync, "result": result}


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
