"""Public UAV agent tool wrappers and registry.

Heavy NFZ-aware route replanning logic lives in `uav_agent.replan` to keep this module
focused on top-level simulator/UTM operations and tool registration.
"""

from __future__ import annotations

from typing import List

from langchain.tools import tool

from .command_adapter import execute_uav_control
from .replan import REPLAN_PROFILE_PRESETS, uav_replan_route_via_utm_nfz
from .simulator import SIM
from utm_agent.service import UTM_SERVICE


@tool
def uav_plan_route(
    uav_id: str = "uav-1",
    route_id: str = "route-1",
    waypoints: List[dict] | None = None,
) -> dict:
    """Plan a UAV route in the local flight simulator."""
    sim = SIM.plan_route(uav_id=uav_id, route_id=route_id, waypoints=waypoints)
    return {
        "status": "success",
        "agent": "uav",
        "result": sim,
    }


@tool
def uav_request_utm_approval(
    uav_id: str = "uav-1",
    airspace_segment: str = "sector-A3",
    operator_license_id: str = "op-001",
    required_license_class: str = "VLOS",
    planned_start_at: str = "",
    planned_end_at: str = "",
    requested_speed_mps: float = 12.0,
) -> dict:
    """Request UTM flight-path approval for the current simulated route."""
    sim = SIM.status(uav_id)
    geofence = UTM_SERVICE.check_no_fly_zones(list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else [])
    SIM.set_geofence_result(uav_id, geofence)
    approval = UTM_SERVICE.verify_flight_plan(
        uav_id=uav_id,
        airspace_segment=airspace_segment,
        route_id=str(sim.get("route_id", "route-1")),
        waypoints=list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else None,
        requested_speed_mps=float(requested_speed_mps),
        planned_start_at=planned_start_at or None,
        planned_end_at=planned_end_at or None,
        operator_license_id=operator_license_id or None,
        required_license_class=required_license_class,
    )
    SIM.set_approval(uav_id, approval)
    return {"status": "success", "agent": "uav", "result": {"approval": approval, "uav": SIM.status(uav_id)}}


@tool
def uav_submit_route_to_utm_geofence_check(
    uav_id: str = "uav-1",
    airspace_segment: str = "sector-A3",
) -> dict:
    """Submit current planned route to UTM geofence/no-fly-zone checks before final approval."""
    sim = SIM.status(uav_id)
    waypoints = list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else []
    nfz = UTM_SERVICE.check_no_fly_zones(waypoints)
    route_bounds = UTM_SERVICE.check_route_bounds(airspace_segment, waypoints)
    out_of_bounds = list(route_bounds.get("out_of_bounds") or [])
    bounds_ok = bool(
        route_bounds.get("ok") is True
        or route_bounds.get("geofence_ok") is True
        or route_bounds.get("bounds_ok") is True
    )
    geofence_result = {
        "ok": bool(bounds_ok and nfz.get("ok", False)),
        "geofence_ok": bounds_ok,
        "bounds_ok": bounds_ok,
        "airspace_segment": airspace_segment,
        "out_of_bounds": out_of_bounds,
        "bounds": route_bounds.get("bounds"),
        "matched_airspace": route_bounds.get("matched_airspace"),
        "source": route_bounds.get("source"),
        "no_fly_zone": nfz,
    }
    SIM.set_geofence_result(uav_id, geofence_result)
    return {"status": "success", "agent": "uav", "result": {"uav_id": uav_id, "route_id": sim.get("route_id"), "geofence": geofence_result}}


@tool
def uav_launch(uav_id: str = "uav-1", require_utm_approval: bool = True) -> dict:
    """Launch UAV mission in the simulator. Can auto-check for stored UTM approval."""
    try:
        if require_utm_approval:
            sim = SIM.status(uav_id)
            route_id = str(sim.get("route_id", "route-1"))
            existing = UTM_SERVICE.get_approval(uav_id, route_id)
            if existing:
                SIM.set_approval(uav_id, existing)
            launch_check = UTM_SERVICE.validate_approval_for_launch(existing, uav_id=uav_id, route_id=route_id)
            if not launch_check.get("ok"):
                return {
                    "status": "error",
                    "agent": "uav",
                    "tool": "uav_launch",
                    "error": f"UTM launch clearance failed: {launch_check.get('error')}",
                    "details": launch_check.get("details"),
                    "hint": "Re-run uav_request_utm_approval or check UTM weather/no-fly/regulation tools.",
                }
        control = execute_uav_control("launch", uav_id=uav_id)
        if control.get("status") != "success":
            return {
                "status": "error",
                "agent": "uav",
                "tool": "uav_launch",
                "error": str(control.get("error") or "launch_failed"),
                "control_adapter": control.get("control_adapter"),
                "adapter_result": control.get("adapter_result"),
                "hint": "Check live control adapter settings or fall back to simulator mode.",
            }
        return {
            "status": "success",
            "agent": "uav",
            "result": control.get("result"),
            "control_adapter": control.get("control_adapter"),
            "adapter_result": control.get("adapter_result"),
        }
    except Exception as e:
        return {"status": "error", "agent": "uav", "tool": "uav_launch", "error": str(e), "hint": "Call uav_request_utm_approval first."}


@tool
def uav_sim_step(uav_id: str = "uav-1", ticks: int = 1) -> dict:
    """Advance UAV mission state by one or more ticks (sim or configured control adapter)."""
    try:
        control = execute_uav_control("step", uav_id=uav_id, params={"ticks": int(ticks)})
        if control.get("status") != "success":
            return {
                "status": "error",
                "agent": "uav",
                "tool": "uav_sim_step",
                "error": str(control.get("error") or "step_failed"),
                "control_adapter": control.get("control_adapter"),
                "adapter_result": control.get("adapter_result"),
            }
        return {
            "status": "success",
            "agent": "uav",
            "result": control.get("result"),
            "control_adapter": control.get("control_adapter"),
            "adapter_result": control.get("adapter_result"),
        }
    except Exception as e:
        return {"status": "error", "agent": "uav", "tool": "uav_sim_step", "error": str(e)}


@tool
def uav_status(uav_id: str = "uav-1") -> dict:
    """Return simulator UAV status."""
    return {"status": "success", "agent": "uav", "result": SIM.status(uav_id)}


@tool
def uav_hold(uav_id: str = "uav-1", reason: str = "operator_request") -> dict:
    """Command UAV hold/loiter via configured control adapter."""
    control = execute_uav_control("hold", uav_id=uav_id, params={"reason": reason})
    if control.get("status") != "success":
        return {
            "status": "error",
            "agent": "uav",
            "tool": "uav_hold",
            "error": str(control.get("error") or "hold_failed"),
            "control_adapter": control.get("control_adapter"),
            "adapter_result": control.get("adapter_result"),
        }
    return {
        "status": "success",
        "agent": "uav",
        "result": control.get("result"),
        "control_adapter": control.get("control_adapter"),
        "adapter_result": control.get("adapter_result"),
    }


@tool
def uav_resume(uav_id: str = "uav-1") -> dict:
    """Resume UAV mission after hold/pause via configured control adapter."""
    try:
        control = execute_uav_control("resume", uav_id=uav_id)
        if control.get("status") != "success":
            return {
                "status": "error",
                "agent": "uav",
                "tool": "uav_resume",
                "error": str(control.get("error") or "resume_failed"),
                "control_adapter": control.get("control_adapter"),
                "adapter_result": control.get("adapter_result"),
            }
        return {
            "status": "success",
            "agent": "uav",
            "result": control.get("result"),
            "control_adapter": control.get("control_adapter"),
            "adapter_result": control.get("adapter_result"),
        }
    except Exception as e:
        return {"status": "error", "agent": "uav", "tool": "uav_resume", "error": str(e)}


@tool
def uav_return_to_home(uav_id: str = "uav-1") -> dict:
    """Command UAV return-to-home via configured control adapter."""
    control = execute_uav_control("rth", uav_id=uav_id)
    if control.get("status") != "success":
        return {
            "status": "error",
            "agent": "uav",
            "tool": "uav_return_to_home",
            "error": str(control.get("error") or "rth_failed"),
            "control_adapter": control.get("control_adapter"),
            "adapter_result": control.get("adapter_result"),
        }
    return {
        "status": "success",
        "agent": "uav",
        "result": control.get("result"),
        "control_adapter": control.get("control_adapter"),
        "adapter_result": control.get("adapter_result"),
    }


@tool
def uav_land(uav_id: str = "uav-1") -> dict:
    """Command UAV landing via configured control adapter."""
    control = execute_uav_control("land", uav_id=uav_id)
    if control.get("status") != "success":
        return {
            "status": "error",
            "agent": "uav",
            "tool": "uav_land",
            "error": str(control.get("error") or "land_failed"),
            "control_adapter": control.get("control_adapter"),
            "adapter_result": control.get("adapter_result"),
        }
    return {
        "status": "success",
        "agent": "uav",
        "result": control.get("result"),
        "control_adapter": control.get("control_adapter"),
        "adapter_result": control.get("adapter_result"),
    }


TOOLS = [
    uav_plan_route,
    uav_submit_route_to_utm_geofence_check,
    uav_request_utm_approval,
    uav_launch,
    uav_sim_step,
    uav_status,
    uav_hold,
    uav_replan_route_via_utm_nfz,
    uav_resume,
    uav_return_to_home,
    uav_land,
]


__all__ = [
    "REPLAN_PROFILE_PRESETS",
    "TOOLS",
    "uav_hold",
    "uav_land",
    "uav_launch",
    "uav_plan_route",
    "uav_replan_route_via_utm_nfz",
    "uav_request_utm_approval",
    "uav_resume",
    "uav_return_to_home",
    "uav_sim_step",
    "uav_status",
    "uav_submit_route_to_utm_geofence_check",
]
