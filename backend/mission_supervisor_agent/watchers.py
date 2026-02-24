from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from network_agent.service import NETWORK_MISSION_SERVICE
from uav_agent.simulator import SIM
from utm_agent.service import UTM_SERVICE

from .state import MissionState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ctx(state: MissionState) -> Dict[str, Any]:
    mission = state.get("mission") or {}
    metadata = mission.get("metadata") if isinstance(mission, dict) else {}
    intent = state.get("intent") or {}
    params = {}
    if isinstance(intent, dict):
        maybe = intent.get("params")
        if isinstance(maybe, dict):
            params = maybe
    uav_state = state.get("uav_state_snapshot") or state.get("uav_state") or {}
    route_id = "route-1"
    if isinstance(uav_state, dict):
        route_id = str(uav_state.get("route_id") or route_id)
    return {
        "uav_id": str((params.get("uav_id") if isinstance(params, dict) else None) or (metadata.get("uav_id") if isinstance(metadata, dict) else None) or "uav-1"),
        "airspace_segment": str(
            (params.get("airspace_segment") if isinstance(params, dict) else None)
            or (metadata.get("airspace_segment") if isinstance(metadata, dict) else None)
            or "sector-A3"
        ),
        "operator_license_id": str(
            (params.get("operator_license_id") if isinstance(params, dict) else None)
            or (metadata.get("operator_license_id") if isinstance(metadata, dict) else None)
            or "op-001"
        ),
        "required_license_class": str(
            (params.get("required_license_class") if isinstance(params, dict) else None)
            or (metadata.get("required_license_class") if isinstance(metadata, dict) else None)
            or "VLOS"
        ),
        "requested_speed_mps": float(
            (params.get("requested_speed_mps") if isinstance(params, dict) else None)
            or (metadata.get("requested_speed_mps") if isinstance(metadata, dict) else None)
            or 12.0
        ),
        "route_id": route_id,
    }


def _append_event(state: MissionState, event_type: str, *, source: str, severity: str = "info", data: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    events = list(state.get("events") or [])
    events.append({"ts": _utc_now(), "type": event_type, "source": source, "severity": severity, "data": dict(data or {})})
    return events


def refresh_uav_state(state: MissionState) -> MissionState:
    c = _ctx(state)
    try:
        snap = SIM.status(c["uav_id"])
        phase = str(snap.get("flight_phase") or "UNKNOWN")
        events = _append_event(
            state,
            "uav_state_refreshed",
            source="watcher.uav",
            data={"uav_id": c["uav_id"], "flight_phase": phase, "battery_pct": snap.get("battery_pct")},
        )
        mission_phase = str(state.get("mission_phase") or "preflight")
        if phase in {"TAKEOFF"}:
            mission_phase = "launch"
        elif phase in {"MISSION"}:
            mission_phase = "execution"
        elif phase in {"HOLD", "LOW_BATTERY", "RTH"}:
            mission_phase = "mitigation"
        elif phase in {"ARRIVAL", "LAND", "LOITER"}:
            mission_phase = "closeout"
        return {
            "uav_state": snap,
            "uav_state_snapshot": snap,
            "events": events,
            "mission_phase": mission_phase,
            "mission_status": str(state.get("mission_status") or "observing"),
            "status": "uav_state_refreshed",
        }
    except Exception as e:
        events = _append_event(state, "uav_state_refresh_failed", source="watcher.uav", severity="error", data={"error": str(e)})
        return {"events": events, "status": "uav_state_refresh_failed"}


def refresh_utm_state(state: MissionState) -> MissionState:
    c = _ctx(state)
    try:
        uav = state.get("uav_state_snapshot") or state.get("uav_state") or {}
        waypoints = list(uav.get("waypoints") or []) if isinstance(uav, dict) else []
        utm_snap = {
            "airspace_segment": c["airspace_segment"],
            "weather": UTM_SERVICE.get_weather(c["airspace_segment"]),
            "weather_check": UTM_SERVICE.check_weather(c["airspace_segment"]),
            "no_fly_zone_check": UTM_SERVICE.check_no_fly_zones(waypoints),
            "regulation_check": UTM_SERVICE.check_regulations(waypoints, requested_speed_mps=c["requested_speed_mps"]),
            "license_check": UTM_SERVICE.check_operator_license(
                operator_license_id=c["operator_license_id"], required_class=c["required_license_class"]
            ),
            "approvals_store": dict(UTM_SERVICE.approvals),
        }
        severe = []
        if not bool((utm_snap.get("weather_check") or {}).get("ok", True)):
            severe.append("weather")
        if not bool((utm_snap.get("no_fly_zone_check") or {}).get("ok", True)):
            severe.append("nfz")
        if not bool((utm_snap.get("regulation_check") or {}).get("ok", True)):
            severe.append("regulation")
        if not bool((utm_snap.get("license_check") or {}).get("ok", True)):
            severe.append("license")
        events = _append_event(
            state,
            "utm_state_refreshed",
            source="watcher.utm",
            severity="warning" if severe else "info",
            data={"airspace_segment": c["airspace_segment"], "issues": severe},
        )
        return {
            "utm_state": utm_snap,
            "utm_state_snapshot": utm_snap,
            "events": events,
            "status": "utm_state_refreshed",
        }
    except Exception as e:
        events = _append_event(state, "utm_state_refresh_failed", source="watcher.utm", severity="error", data={"error": str(e)})
        return {"events": events, "status": "utm_state_refresh_failed"}


def refresh_network_state(state: MissionState) -> MissionState:
    c = _ctx(state)
    try:
        raw = NETWORK_MISSION_SERVICE.get_state(airspace_segment=c["airspace_segment"], selected_uav_id=c["uav_id"])
        snap = dict(raw.get("result") or {}) if isinstance(raw, dict) else {}
        kpis = dict(snap.get("networkKpis") or {}) if isinstance(snap, dict) else {}
        risk_count = int(kpis.get("highInterferenceRiskCount", 0) or 0)
        events = _append_event(
            state,
            "network_state_refreshed",
            source="watcher.network",
            severity="warning" if risk_count > 0 else "info",
            data={
                "airspace_segment": c["airspace_segment"],
                "coverageScorePct": kpis.get("coverageScorePct"),
                "avgLatencyMs": kpis.get("avgLatencyMs"),
                "highInterferenceRiskCount": risk_count,
            },
        )
        return {
            "network_state": snap,
            "network_state_snapshot": snap,
            "events": events,
            "status": "network_state_refreshed",
        }
    except Exception as e:
        events = _append_event(state, "network_state_refresh_failed", source="watcher.network", severity="error", data={"error": str(e)})
        return {"events": events, "status": "network_state_refresh_failed"}


def ingest_events(state: MissionState) -> MissionState:
    events = list(state.get("events") or [])
    uav = dict(state.get("uav_state_snapshot") or {})
    utm = dict(state.get("utm_state_snapshot") or {})
    net = dict(state.get("network_state_snapshot") or {})

    warnings: List[str] = []
    if isinstance(uav, dict):
        battery = float(uav.get("battery_pct", 100.0) or 100.0)
        if battery < 20.0:
            warnings.append("uav_low_battery")
    if isinstance(utm, dict):
        for k in ("weather_check", "no_fly_zone_check", "regulation_check", "license_check"):
            check = utm.get(k)
            if isinstance(check, dict) and check.get("ok") is False:
                warnings.append(f"utm_{k}_failed")
    if isinstance(net, dict):
        kpis = net.get("networkKpis")
        if isinstance(kpis, dict):
            if float(kpis.get("coverageScorePct", 100.0) or 100.0) < 85.0:
                warnings.append("network_coverage_low")
            if float(kpis.get("avgLatencyMs", 0.0) or 0.0) > 35.0:
                warnings.append("network_latency_high")
            if int(kpis.get("highInterferenceRiskCount", 0) or 0) > 0:
                warnings.append("network_interference_risk")

    summary = {
        "ts": _utc_now(),
        "watchers": {
            "uav": "ok" if uav else "missing",
            "utm": "ok" if utm else "missing",
            "network": "ok" if net else "missing",
        },
        "warning_count": len(warnings),
        "warnings": warnings,
        "uav": {
            "uav_id": uav.get("uav_id"),
            "flight_phase": uav.get("flight_phase"),
            "battery_pct": uav.get("battery_pct"),
        },
        "utm": {
            "airspace_segment": utm.get("airspace_segment"),
            "weather_ok": (utm.get("weather_check") or {}).get("ok") if isinstance(utm.get("weather_check"), dict) else None,
            "license_ok": (utm.get("license_check") or {}).get("ok") if isinstance(utm.get("license_check"), dict) else None,
        },
        "network": {
            "coverageScorePct": ((net.get("networkKpis") or {}).get("coverageScorePct") if isinstance(net.get("networkKpis"), dict) else None),
            "avgLatencyMs": ((net.get("networkKpis") or {}).get("avgLatencyMs") if isinstance(net.get("networkKpis"), dict) else None),
            "highInterferenceRiskCount": (
                (net.get("networkKpis") or {}).get("highInterferenceRiskCount") if isinstance(net.get("networkKpis"), dict) else None
            ),
        },
    }
    events.append(
        {
            "ts": summary["ts"],
            "type": "mission_watchers_ingested",
            "source": "watcher.ingest",
            "severity": "warning" if warnings else "info",
            "data": {"warnings": warnings},
        }
    )
    return {
        "events": events,
        "mission_state_snapshot": summary,
        "mission_status": "observed",
        "status": "watchers_ingested",
    }


__all__ = [
    "refresh_uav_state",
    "refresh_utm_state",
    "refresh_network_state",
    "ingest_events",
]

