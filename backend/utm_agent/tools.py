from langchain.tools import tool

from agent_db import AgentDB
from .operational_intents import delete_intent as dss_delete_intent
from .operational_intents import query_intents as dss_query_intents
from .operational_intents import upsert_intent as dss_upsert_intent
from .service import UTM_SERVICE
from .subscriptions import delete_subscription as dss_delete_subscription
from .subscriptions import impacted_subscriptions as dss_impacted_subscriptions
from .subscriptions import query_subscriptions as dss_query_subscriptions
from .subscriptions import upsert_subscription as dss_upsert_subscription
from datetime import datetime, timezone
from uuid import uuid4


_UTM_DB = AgentDB("utm")


def _get_dss_operational_intents() -> dict[str, dict]:
    raw = _UTM_DB.get_state("dss_operational_intents")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _set_dss_operational_intents(values: dict[str, dict]) -> None:
    _UTM_DB.set_state("dss_operational_intents", values)


def _get_dss_subscriptions() -> dict[str, dict]:
    raw = _UTM_DB.get_state("dss_subscriptions")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _set_dss_subscriptions(values: dict[str, dict]) -> None:
    _UTM_DB.set_state("dss_subscriptions", values)


def _get_dss_participants() -> dict[str, dict]:
    raw = _UTM_DB.get_state("dss_participants")
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _set_dss_participants(values: dict[str, dict]) -> None:
    _UTM_DB.set_state("dss_participants", values)


def _get_dss_notifications() -> list[dict]:
    raw = _UTM_DB.get_state("dss_notifications")
    if not isinstance(raw, list):
        return []
    return [dict(v) for v in raw if isinstance(v, dict)]


def _set_dss_notifications(values: list[dict]) -> None:
    _UTM_DB.set_state("dss_notifications", values)


def _queue_notifications(notifications: list[dict], *, event_type: str, source_intent_id: str = "") -> list[dict]:
    if not notifications:
        return []
    queue = _get_dss_notifications()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    added: list[dict] = []
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
            "status": "pending",
            "created_at": now,
            "acked_at": None,
        }
        queue.append(row)
        added.append(row)
    _set_dss_notifications(queue[-5000:])
    return added


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


@tool
def utm_dss_upsert_operational_intent(
    intent_id: str = "",
    manager_uss_id: str = "uss-local",
    state: str = "accepted",
    priority: str = "normal",
    conflict_policy: str = "reject",
    uss_base_url: str = "",
    volume4d: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create or update a DSS-style operational intent with strategic conflict checks."""
    intents = _get_dss_operational_intents()
    out = dss_upsert_intent(
        intents,
        intent_id=intent_id or None,
        manager_uss_id=manager_uss_id,
        state=state,
        priority=priority,
        conflict_policy=conflict_policy,
        uss_base_url=uss_base_url or None,
        volume4d=volume4d or {},
        metadata=metadata or {},
    )
    if out.get("stored"):
        _set_dss_operational_intents(intents)
    subscriptions = _get_dss_subscriptions()
    intent = out.get("intent") if isinstance(out.get("intent"), dict) else {}
    event_type = "create" if int(intent.get("version", 1) or 1) <= 1 else "update"
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=intent.get("volume4d") if isinstance(intent.get("volume4d"), dict) else (volume4d or {}),
            event_type=event_type,
        )
        if out.get("stored")
        else []
    )
    queued = _queue_notifications(notifications, event_type=event_type, source_intent_id=str(intent.get("intent_id") or ""))
    return {"status": "success", "agent": "utm", "result": {**out, "subscriptions_to_notify": notifications, "queued_notifications": queued}}


@tool
def utm_dss_query_operational_intents(
    manager_uss_id: str = "",
    states: list[str] | None = None,
    volume4d: dict | None = None,
) -> dict:
    """Query DSS-style operational intents with optional state and 4D volume filters."""
    intents = _get_dss_operational_intents()
    items = dss_query_intents(
        intents,
        manager_uss_id=manager_uss_id or None,
        states=states,
        volume4d=volume4d,
    )
    return {"status": "success", "agent": "utm", "result": {"count": len(items), "items": items}}


@tool
def utm_dss_delete_operational_intent(intent_id: str) -> dict:
    """Delete a DSS-style operational intent."""
    intents = _get_dss_operational_intents()
    before = intents.get(intent_id) if isinstance(intents.get(intent_id), dict) else {}
    out = dss_delete_intent(intents, intent_id)
    if out.get("deleted"):
        _set_dss_operational_intents(intents)
    subscriptions = _get_dss_subscriptions()
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=before.get("volume4d") if isinstance(before, dict) and isinstance(before.get("volume4d"), dict) else {},
            event_type="delete",
        )
        if out.get("deleted")
        else []
    )
    queued = _queue_notifications(notifications, event_type="delete", source_intent_id=str(intent_id))
    return {"status": "success", "agent": "utm", "result": {**out, "subscriptions_to_notify": notifications, "queued_notifications": queued}}


@tool
def utm_dss_upsert_subscription(
    subscription_id: str = "",
    manager_uss_id: str = "uss-local",
    uss_base_url: str = "",
    callback_url: str = "",
    volume4d: dict | None = None,
    notify_for: list[str] | None = None,
    expires_at: str = "",
    metadata: dict | None = None,
) -> dict:
    """Create or update a DSS-style airspace subscription."""
    subscriptions = _get_dss_subscriptions()
    out = dss_upsert_subscription(
        subscriptions,
        subscription_id=subscription_id or None,
        manager_uss_id=manager_uss_id,
        uss_base_url=uss_base_url,
        callback_url=callback_url,
        volume4d=volume4d or {},
        notify_for=notify_for,
        expires_at=expires_at or None,
        metadata=metadata or {},
    )
    _set_dss_subscriptions(subscriptions)
    return {"status": "success", "agent": "utm", "result": out}


@tool
def utm_dss_query_subscriptions(manager_uss_id: str = "", volume4d: dict | None = None) -> dict:
    """Query DSS-style subscriptions by USS and/or 4D volume."""
    subscriptions = _get_dss_subscriptions()
    items = dss_query_subscriptions(
        subscriptions,
        manager_uss_id=manager_uss_id or None,
        volume4d=volume4d,
    )
    return {"status": "success", "agent": "utm", "result": {"count": len(items), "items": items}}


@tool
def utm_dss_delete_subscription(subscription_id: str) -> dict:
    """Delete a DSS-style subscription."""
    subscriptions = _get_dss_subscriptions()
    out = dss_delete_subscription(subscriptions, subscription_id)
    if out.get("deleted"):
        _set_dss_subscriptions(subscriptions)
    return {"status": "success", "agent": "utm", "result": out}


@tool
def utm_dss_upsert_participant(
    participant_id: str,
    uss_base_url: str = "http://127.0.0.1:9000",
    roles: list[str] | None = None,
    status: str = "active",
    metadata: dict | None = None,
) -> dict:
    """Create or update a local DSS participant registry entry."""
    participants = _get_dss_participants()
    pid = str(participant_id or "").strip()
    if not pid:
        return {"status": "error", "agent": "utm", "error": "participant_id_required"}
    prev = participants.get(pid) if isinstance(participants.get(pid), dict) else {}
    row = {
        "participant_id": pid,
        "uss_base_url": str(uss_base_url or ""),
        "roles": [str(r).strip().lower() for r in (roles or ["uss"]) if str(r).strip()],
        "status": str(status or "active").strip().lower() or "active",
        "metadata": dict(metadata or {}),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version": int(prev.get("version", 0) or 0) + 1 if isinstance(prev, dict) else 1,
    }
    participants[pid] = row
    _set_dss_participants(participants)
    return {"status": "success", "agent": "utm", "result": row}


@tool
def utm_dss_query_participants(status: str = "") -> dict:
    """Query local DSS participants."""
    participants = _get_dss_participants()
    items = list(participants.values())
    if str(status or "").strip():
        items = [x for x in items if str(x.get("status", "")).lower() == str(status).strip().lower()]
    return {"status": "success", "agent": "utm", "result": {"count": len(items), "items": items}}


@tool
def utm_dss_delete_participant(participant_id: str) -> dict:
    """Delete a local DSS participant."""
    participants = _get_dss_participants()
    existed = participants.pop(str(participant_id), None)
    _set_dss_participants(participants)
    return {"status": "success", "agent": "utm", "result": {"deleted": existed is not None, "participant_id": participant_id}}


@tool
def utm_dss_query_notifications(limit: int = 100, status: str = "", subscription_id: str = "") -> dict:
    """Query local DSS notification queue."""
    notifications = _get_dss_notifications()
    items = notifications
    if str(status or "").strip():
        items = [n for n in items if str(n.get("status", "")).lower() == str(status).strip().lower()]
    if str(subscription_id or "").strip():
        items = [n for n in items if str(n.get("subscription_id", "")) == str(subscription_id).strip()]
    lim = max(1, min(1000, int(limit)))
    items = list(reversed(items))[:lim]
    return {"status": "success", "agent": "utm", "result": {"count": len(items), "items": items}}


@tool
def utm_dss_ack_notification(notification_id: str) -> dict:
    """Acknowledge a DSS notification."""
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
    return {"status": "success", "agent": "utm", "result": {"acked": found, "notification_id": notification_id}}


@tool
def utm_dss_run_local_conformance() -> dict:
    """Run lightweight local DSS conformance checks."""
    intents: dict[str, dict] = {}
    subscriptions: dict[str, dict] = {}
    scenarios: list[dict] = []

    a = dss_upsert_intent(
        intents,
        intent_id="tool-conf-a",
        manager_uss_id="uss-a",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d={"x": [0, 40], "y": [0, 40], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
    )
    b = dss_upsert_intent(
        intents,
        intent_id="tool-conf-b",
        manager_uss_id="uss-b",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d={"x": [50, 90], "y": [50, 90], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
    )
    scenarios.append({"scenario": "non_overlap", "passed": bool(a.get("stored") and b.get("stored"))})

    c = dss_upsert_intent(
        intents,
        intent_id="tool-conf-c",
        manager_uss_id="uss-c",
        state="accepted",
        priority="normal",
        conflict_policy="reject",
        volume4d={"x": [35, 55], "y": [35, 55], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
    )
    scenarios.append({"scenario": "overlap_reject", "passed": bool(c.get("status") == "rejected" and not c.get("stored"))})

    dss_upsert_subscription(
        subscriptions,
        subscription_id="tool-sub-1",
        manager_uss_id="uss-watch",
        callback_url="local://watch/callback",
        volume4d={"x": [0, 120], "y": [0, 120], "z": [0, 120], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T13:00:00Z"},
        notify_for=["create", "update", "delete"],
    )
    d = dss_upsert_intent(
        intents,
        intent_id="tool-conf-d",
        manager_uss_id="uss-d",
        state="accepted",
        priority="high",
        conflict_policy="conditional_approve",
        volume4d={"x": [10, 15], "y": [10, 15], "z": [0, 100], "time_start": "2026-02-27T12:01:00Z", "time_end": "2026-02-27T12:10:00Z"},
    )
    impacted = dss_impacted_subscriptions(
        subscriptions,
        changed_volume4d=(d.get("intent") or {}).get("volume4d", {"x": [0, 0], "y": [0, 0], "z": [0, 0], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:01:00Z"}),
        event_type="create",
    )
    scenarios.append({"scenario": "notification_impacted", "passed": bool(len(impacted) >= 1)})

    passed = all(bool(s.get("passed")) for s in scenarios)
    return {
        "status": "success",
        "agent": "utm",
        "result": {"passed": passed, "total": len(scenarios), "passed_count": len([s for s in scenarios if s.get("passed")]), "scenarios": scenarios},
    }


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
    utm_dss_upsert_operational_intent,
    utm_dss_query_operational_intents,
    utm_dss_delete_operational_intent,
    utm_dss_upsert_subscription,
    utm_dss_query_subscriptions,
    utm_dss_delete_subscription,
    utm_dss_upsert_participant,
    utm_dss_query_participants,
    utm_dss_delete_participant,
    utm_dss_query_notifications,
    utm_dss_ack_notification,
    utm_dss_run_local_conformance,
]
