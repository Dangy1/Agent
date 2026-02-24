import math
from typing import Any, List

from langchain.tools import tool

from .simulator import SIM
from network_agent.service import NETWORK_MISSION_SERVICE
from utm_agent.service import UTM_SERVICE


def _normalize_route_id_base(route_id: str | None) -> str:
    rid = (route_id or "route-1").strip() or "route-1"
    # Remove repeated workflow suffixes so IDs stay readable across multiple replans/reschedules.
    suffixes = ("-replan", "-reschedule")
    changed = True
    while changed and rid:
        changed = False
        for s in suffixes:
            if rid.endswith(s):
                rid = rid[: -len(s)] or "route-1"
                changed = True
    return rid


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
    bounds = {"sector-A3": {"x": [0, 400], "y": [0, 300], "z": [0, 120]}}
    seg = bounds.get(airspace_segment, {"x": [-1e9, 1e9], "y": [-1e9, 1e9], "z": [0, 120]})
    out_of_bounds = []
    for i, wp in enumerate(waypoints):
        x = float(wp.get("x", 0.0)); y = float(wp.get("y", 0.0)); z = float(wp.get("z", 0.0))
        if not (seg["x"][0] <= x <= seg["x"][1] and seg["y"][0] <= y <= seg["y"][1] and seg["z"][0] <= z <= seg["z"][1]):
            out_of_bounds.append({"index": i, "wp": {"x": x, "y": y, "z": z}})
    geofence_result = {
        "ok": len(out_of_bounds) == 0 and nfz.get("ok", False),
        "airspace_segment": airspace_segment,
        "out_of_bounds": out_of_bounds,
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
        snap = SIM.launch(uav_id)
        return {"status": "success", "agent": "uav", "result": snap}
    except Exception as e:
        return {"status": "error", "agent": "uav", "tool": "uav_launch", "error": str(e), "hint": "Call uav_request_utm_approval first."}


@tool
def uav_sim_step(uav_id: str = "uav-1", ticks: int = 1) -> dict:
    """Advance the simulated UAV mission by one or more ticks."""
    try:
        return {"status": "success", "agent": "uav", "result": SIM.step(uav_id, ticks=ticks)}
    except Exception as e:
        return {"status": "error", "agent": "uav", "tool": "uav_sim_step", "error": str(e)}


@tool
def uav_status(uav_id: str = "uav-1") -> dict:
    """Return simulator UAV status."""
    return {"status": "success", "agent": "uav", "result": SIM.status(uav_id)}


@tool
def uav_hold(uav_id: str = "uav-1", reason: str = "operator_request") -> dict:
    """Command UAV to hold/loiter in simulator."""
    return {"status": "success", "agent": "uav", "result": SIM.hold(uav_id, reason)}


def _parse_replan_preferences(user_request: str, optimization_profile: str = "balanced") -> dict[str, Any]:
    t = (user_request or "").lower()
    if any(k in t for k in ["north", "upward", "upper", "top"]):
        side = "north"
    elif any(k in t for k in ["south", "downward", "lower side", "bottom"]):
        side = "south"
    elif any(k in t for k in ["east", "right"]):
        side = "east"
    elif any(k in t for k in ["west", "left"]):
        side = "west"
    else:
        side = "auto"

    altitude_delta = 0.0
    if any(k in t for k in ["higher", "increase altitude", "climb", "raise altitude"]):
        altitude_delta = 20.0
    elif any(k in t for k in ["lower altitude", "descend", "reduce altitude"]):
        altitude_delta = -10.0

    margin_m = 15.0
    if any(k in t for k in ["wide", "extra margin", "safer distance"]):
        margin_m = 30.0
    profile = str(optimization_profile or "balanced").lower()
    if profile not in {"safe", "balanced", "aggressive"}:
        profile = "balanced"
    if profile == "safe":
        margin_m = max(margin_m, 22.0)
        simplification_signal_floor_dbm = -90.0
        simplification_max_segment_m = 1800.0
    elif profile == "aggressive":
        margin_m = max(10.0, margin_m - 5.0)
        simplification_signal_floor_dbm = -100.0
        simplification_max_segment_m = 3200.0
    else:
        simplification_signal_floor_dbm = -96.0
        simplification_max_segment_m = 2600.0
    return {
        "side": side,
        "altitude_delta": altitude_delta,
        "margin_m": margin_m,
        "optimization_profile": profile,
        "simplification_signal_floor_dbm": simplification_signal_floor_dbm,
        "simplification_max_segment_m": simplification_max_segment_m,
    }


def _move_point_outside_zone(wp: dict[str, Any], zone: dict[str, Any], prefs: dict[str, Any]) -> dict[str, float]:
    x = float(wp.get("x", 0.0))
    y = float(wp.get("y", 0.0))
    z = float(wp.get("z", 0.0))
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = float(zone.get("radius_m", 0.0)) + float(prefs.get("margin_m", 15.0))
    side = str(prefs.get("side", "auto"))

    if side == "north":
        y = max(y, cy + r)
    elif side == "south":
        y = min(y, cy - r)
    elif side == "east":
        x = max(x, cx + r)
    elif side == "west":
        x = min(x, cx - r)
    else:
        dx = x - cx
        dy = y - cy
        norm = math.hypot(dx, dy) or 1.0
        x = cx + (dx / norm) * r
        y = cy + (dy / norm) * r

    z += float(prefs.get("altitude_delta", 0.0))
    z = max(0.0, min(120.0, z))
    out: dict[str, Any] = {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)}
    if isinstance(wp.get("action"), str):
        out["action"] = str(wp.get("action"))
    return out  # type: ignore[return-value]


def _segment_intersects_circle_2d(a: dict[str, float], b: dict[str, float], cx: float, cy: float, r: float) -> bool:
    ax = float(a.get("x", 0.0))
    ay = float(a.get("y", 0.0))
    bx = float(b.get("x", 0.0))
    by = float(b.get("y", 0.0))
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return (ax - cx) ** 2 + (ay - cy) ** 2 <= r * r
    t = ((cx - ax) * dx + (cy - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px = ax + t * dx
    py = ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _segment_altitude_overlaps_zone(a: dict[str, float], b: dict[str, float], zone: dict[str, Any]) -> bool:
    z1 = float(a.get("z", 0.0))
    z2 = float(b.get("z", 0.0))
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))
    return max(min(z1, z2), z_min) <= min(max(z1, z2), z_max)


def _point_inside_zone_with_margin(wp: dict[str, Any], zone: dict[str, Any], margin_m: float) -> bool:
    x = float(wp.get("x", 0.0))
    y = float(wp.get("y", 0.0))
    z = float(wp.get("z", 0.0))
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = float(zone.get("radius_m", 0.0)) + margin_m
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))
    return ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) and (z_min <= z <= z_max)


def _segment_conflicts_any_zone(a: dict[str, float], b: dict[str, float], zones: list[dict[str, Any]], margin_m: float) -> bool:
    for zone in zones:
        if not _segment_altitude_overlaps_zone(a, b, zone):
            continue
        cx = float(zone.get("cx", 0.0))
        cy = float(zone.get("cy", 0.0))
        r = float(zone.get("radius_m", 0.0)) + margin_m
        if _segment_intersects_circle_2d(a, b, cx, cy, r):
            return True
    return False


def _segment_length(a: dict[str, float], b: dict[str, float]) -> float:
    dx = float(b.get("x", 0.0)) - float(a.get("x", 0.0))
    dy = float(b.get("y", 0.0)) - float(a.get("y", 0.0))
    dz = float(b.get("z", 0.0)) - float(a.get("z", 0.0))
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _min_signal_over_segment(a: dict[str, float], b: dict[str, float], samples: int = 3) -> float:
    # Reuse the network service RF model as a heuristic for route optimization.
    if not NETWORK_MISSION_SERVICE.base_stations:
        return -999.0
    best_min = -999.0
    for i in range(samples):
        t = 0.0 if samples <= 1 else i / (samples - 1)
        p = {
            "x": float(a.get("x", 0.0)) + (float(b.get("x", 0.0)) - float(a.get("x", 0.0))) * t,
            "y": float(a.get("y", 0.0)) + (float(b.get("y", 0.0)) - float(a.get("y", 0.0))) * t,
            "z": float(a.get("z", 0.0)) + (float(b.get("z", 0.0)) - float(a.get("z", 0.0))) * t,
        }
        strongest = max(NETWORK_MISSION_SERVICE._signal_at(bs, p) for bs in NETWORK_MISSION_SERVICE.base_stations)  # type: ignore[attr-defined]
        if i == 0:
            best_min = strongest
        else:
            best_min = min(best_min, strongest)
    return best_min


def _simplify_route_with_constraints(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(route) <= 2:
        return route, []
    margin_m = float(prefs.get("margin_m", 15.0))
    deletions: list[dict[str, Any]] = []
    simplified = [dict(w) for w in route]
    changed = True
    while changed and len(simplified) > 2:
        changed = False
        i = 1
        while i < len(simplified) - 1:
            a = simplified[i - 1]
            cur = simplified[i]
            b = simplified[i + 1]
            # Keep mission-service waypoints (photo/temp/etc) unless clearly redundant transit.
            action = str(cur.get("action", "transit"))
            if action not in {"transit", "hover"}:
                i += 1
                continue
            if _segment_length(a, b) > float(prefs.get("simplification_max_segment_m", 2600.0)):
                i += 1
                continue
            if _segment_conflicts_any_zone(a, b, zones, margin_m):
                i += 1
                continue
            # Keep intermediate point if direct hop would materially reduce coverage.
            seg_signal = _min_signal_over_segment(a, b)
            if seg_signal < float(prefs.get("simplification_signal_floor_dbm", -96.0)):
                i += 1
                continue
            deletions.append({"kind": "waypoint_removed", "index": i, "reason": "redundant_transit", "waypoint": dict(cur)})
            simplified.pop(i)
            changed = True
        # restart another pass if we deleted anything
    return simplified, deletions


def _detour_point_for_zone(a: dict[str, float], b: dict[str, float], zone: dict[str, Any], prefs: dict[str, Any]) -> dict[str, float]:
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = float(zone.get("radius_m", 0.0)) + float(prefs.get("margin_m", 15.0))
    side = str(prefs.get("side", "auto"))
    mx = (float(a.get("x", 0.0)) + float(b.get("x", 0.0))) / 2.0
    my = (float(a.get("y", 0.0)) + float(b.get("y", 0.0))) / 2.0

    if side == "north":
        x, y = cx, cy + r
    elif side == "south":
        x, y = cx, cy - r
    elif side == "east":
        x, y = cx + r, cy
    elif side == "west":
        x, y = cx - r, cy
    else:
        dx = mx - cx
        dy = my - cy
        norm = math.hypot(dx, dy) or 1.0
        x = cx + (dx / norm) * r
        y = cy + (dy / norm) * r

    z_base = max(float(a.get("z", 0.0)), float(b.get("z", 0.0)))
    z = z_base + float(prefs.get("altitude_delta", 0.0))
    if _segment_altitude_overlaps_zone(a, b, zone):
        z = max(z, float(zone.get("z_max", 0.0)) + 5.0)
    return {"x": round(min(400.0, max(0.0, x)), 2), "y": round(min(300.0, max(0.0, y)), 2), "z": round(min(120.0, max(0.0, z)), 2), "action": "transit"}


@tool
def uav_replan_route_via_utm_nfz(
    uav_id: str = "uav-1",
    airspace_segment: str = "sector-A3",
    user_request: str = "",
    route_id: str | None = None,
    waypoints: list[dict] | None = None,
    optimization_profile: str = "balanced",
    route_id_suffix: str = "-replan",
) -> dict:
    """Fetch UTM no-fly-zones and replan current simulated route to avoid NFZs using a simple heuristic."""
    sim = SIM.status(uav_id)
    current = [dict(w) for w in waypoints] if isinstance(waypoints, list) else (list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else [])
    source_route_id = str(route_id or sim.get("route_id", "route-1"))
    if len(current) < 2:
        return {
            "status": "error",
            "agent": "uav",
            "tool": "uav_replan_route_via_utm_nfz",
            "error": "route_requires_at_least_two_waypoints",
        }
    prefs = _parse_replan_preferences(user_request, optimization_profile=optimization_profile)
    nfz_before = UTM_SERVICE.check_no_fly_zones(current)
    zones = list(UTM_SERVICE.no_fly_zones)
    updated: list[dict[str, float]] = []
    changes: list[dict[str, Any]] = []

    for idx, wp in enumerate(current):
        next_wp: dict[str, Any] = {"x": float(wp.get("x", 0.0)), "y": float(wp.get("y", 0.0)), "z": float(wp.get("z", 0.0))}
        if isinstance(wp.get("action"), str):
            next_wp["action"] = str(wp.get("action"))
        for zone in zones:
            cx = float(zone.get("cx", 0.0))
            cy = float(zone.get("cy", 0.0))
            radius = float(zone.get("radius_m", 0.0))
            z_min = float(zone.get("z_min", -1e9))
            z_max = float(zone.get("z_max", 1e9))
            dx = next_wp["x"] - cx
            dy = next_wp["y"] - cy
            inside_xy = (dx * dx + dy * dy) <= radius * radius
            inside_z = z_min <= next_wp["z"] <= z_max
            if inside_xy and inside_z:
                prev = dict(next_wp)
                next_wp = _move_point_outside_zone(next_wp, zone, prefs)
                changes.append({"index": idx, "zone_id": zone.get("zone_id"), "from": prev, "to": dict(next_wp)})
        # keep within simulated bounds for sector-A3 if requested
        if airspace_segment == "sector-A3":
            next_wp["x"] = min(400.0, max(0.0, next_wp["x"]))
            next_wp["y"] = min(300.0, max(0.0, next_wp["y"]))
            next_wp["z"] = min(120.0, max(0.0, next_wp["z"]))
        updated.append(next_wp)

    # Second pass: insert detour waypoints when route segments still intersect NFZ circles.
    segment_insertions: list[dict[str, Any]] = []
    i = 0
    max_insertions = 8
    while i < len(updated) - 1 and len(segment_insertions) < max_insertions:
        a = updated[i]
        b = updated[i + 1]
        inserted = False
        for zone in zones:
            cx = float(zone.get("cx", 0.0))
            cy = float(zone.get("cy", 0.0))
            radius = float(zone.get("radius_m", 0.0)) + float(prefs.get("margin_m", 15.0))
            if not _segment_altitude_overlaps_zone(a, b, zone):
                continue
            if _segment_intersects_circle_2d(a, b, cx, cy, radius):
                detour = _detour_point_for_zone(a, b, zone, prefs)
                # Skip duplicate insertions if detour is effectively same as neighbors.
                if (
                    math.hypot(detour["x"] - a["x"], detour["y"] - a["y"]) < 1.0
                    or math.hypot(detour["x"] - b["x"], detour["y"] - b["y"]) < 1.0
                ):
                    continue
                updated.insert(i + 1, detour)
                rec = {"insert_after_index": i, "zone_id": zone.get("zone_id"), "detour": dict(detour)}
                segment_insertions.append(rec)
                changes.append({"kind": "segment_detour", **rec})
                inserted = True
                break
        if not inserted:
            i += 1

    # Third pass: simplify/reduce route by removing redundant waypoints while preserving NFZ avoidance
    # and basic network quality heuristics.
    updated, deletions = _simplify_route_with_constraints(updated, zones, prefs)
    changes.extend(deletions)

    replanned_route_id = f"{_normalize_route_id_base(source_route_id)}{route_id_suffix}"
    SIM.plan_route(uav_id=uav_id, route_id=replanned_route_id, waypoints=updated)
    nfz_after = UTM_SERVICE.check_no_fly_zones(updated)
    return {
        "status": "success",
        "agent": "uav",
        "result": {
            "uav_id": uav_id,
            "airspace_segment": airspace_segment,
            "source_route_id": source_route_id,
            "route_id": replanned_route_id,
            "user_request": user_request,
            "replan_preferences": prefs,
            "changes": changes,
            "segment_insertions": segment_insertions,
            "waypoint_deletions": deletions,
            "nfz_before": nfz_before,
            "nfz_after": nfz_after,
            "uav": SIM.status(uav_id),
        },
    }


@tool
def uav_resume(uav_id: str = "uav-1") -> dict:
    """Resume UAV mission after hold/pause in simulator."""
    try:
        return {"status": "success", "agent": "uav", "result": SIM.resume(uav_id)}
    except Exception as e:
        return {"status": "error", "agent": "uav", "tool": "uav_resume", "error": str(e)}


@tool
def uav_return_to_home(uav_id: str = "uav-1") -> dict:
    """Command UAV return-to-home in simulator."""
    return {"status": "success", "agent": "uav", "result": SIM.rth(uav_id)}


@tool
def uav_land(uav_id: str = "uav-1") -> dict:
    """Command UAV landing in simulator."""
    return {"status": "success", "agent": "uav", "result": SIM.land(uav_id)}


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
