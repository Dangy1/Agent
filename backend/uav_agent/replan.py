"""UAV route replanning helpers and NFZ-aware replan tool."""

import heapq
import math
from typing import Any, List

from langchain.tools import tool

from .simulator import SIM
from network_agent.service import NETWORK_MISSION_SERVICE
from utm_agent.service import UTM_SERVICE


REPLAN_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    # Treat NFZ as infinite-height in safe mode: must route around the XY footprint.
    "safe": {
        "margin_m": 30.0,
        "simplification_signal_floor_dbm": -90.0,
        "simplification_max_segment_m": 1800.0,
        "segment_repair_max_insertions": 14,
        "nfz_conflict_mode": "xy_only",
        "allow_overflight": False,
        "overflight_clearance_m": 0.0,
        "vertical_cost_weight": 2.0,
        "los_prune_max_passes": 4,
        "description": "treat NFZ as infinite-height XY block; always route around with largest clearance",
    },
    # Balanced behaves like aggressive (finite-height NFZ), but with larger XY/Z buffers.
    "balanced": {
        "margin_m": 15.0,
        "simplification_signal_floor_dbm": -96.0,
        "simplification_max_segment_m": 2600.0,
        "segment_repair_max_insertions": 10,
        "nfz_conflict_mode": "xyz_cylinder",
        "allow_overflight": True,
        "overflight_clearance_m": 15.0,
        "vertical_cost_weight": 1.8,
        "los_prune_max_passes": 4,
        "description": "finite-height NFZ; choose over-vs-around with larger XY and Z buffers",
    },
    # Aggressive uses finite-height NFZ and can choose altitude overflight with small margins.
    "aggressive": {
        "margin_m": 8.0,
        "simplification_signal_floor_dbm": -100.0,
        "simplification_max_segment_m": 3200.0,
        "segment_repair_max_insertions": 8,
        "nfz_conflict_mode": "xyz_cylinder",
        "allow_overflight": True,
        "overflight_clearance_m": 3.0,
        "vertical_cost_weight": 1.35,
        "los_prune_max_passes": 5,
        "description": "finite-height NFZ; choose over-vs-around with small margins for minimum detour",
    },
}


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

    profile = str(optimization_profile or "balanced").lower()
    if profile not in {"safe", "balanced", "aggressive"}:
        profile = "balanced"
    preset = dict(REPLAN_PROFILE_PRESETS.get(profile, REPLAN_PROFILE_PRESETS["balanced"]))
    margin_m = float(preset.get("margin_m", 18.0))
    if any(k in t for k in ["wide", "extra margin", "safer distance", "gps error", "tracking error", "false alarm"]):
        margin_m = max(margin_m, 30.0)
    elif any(k in t for k in ["tight", "closer", "shorter detour", "faster path"]):
        margin_m = max(4.0, margin_m - 4.0)
    return {
        "side": side,
        "altitude_delta": altitude_delta,
        "margin_m": margin_m,
        "optimization_profile": profile,
        "simplification_signal_floor_dbm": float(preset.get("simplification_signal_floor_dbm", -96.0)),
        "simplification_max_segment_m": float(preset.get("simplification_max_segment_m", 2600.0)),
        "segment_repair_max_insertions": int(preset.get("segment_repair_max_insertions", 10)),
        "nfz_conflict_mode": str(preset.get("nfz_conflict_mode", "xyz_cylinder")),
        "allow_overflight": bool(preset.get("allow_overflight", False)),
        "overflight_clearance_m": float(preset.get("overflight_clearance_m", 3.0)),
        "vertical_cost_weight": float(preset.get("vertical_cost_weight", 1.5)),
        "los_prune_max_passes": int(preset.get("los_prune_max_passes", 4)),
        "profile_definition": str(preset.get("description", "")),
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


def _waypoint_source_tag(wp: dict[str, Any]) -> str:
    return str(wp.get("_wp_source", "") or "")


def _is_replan_generated_waypoint(wp: dict[str, Any]) -> bool:
    return _waypoint_source_tag(wp).startswith("utm_replan")


def _mark_original_waypoint(wp: dict[str, Any]) -> dict[str, Any]:
    out = dict(wp)
    out.setdefault("_wp_origin", "original")
    out.setdefault("_wp_source", "user_planned")
    return out


def _mark_agent_inserted_waypoint(wp: dict[str, Any], source_tag: str) -> dict[str, Any]:
    out = dict(wp)
    out["_wp_origin"] = "agent_inserted"
    out["_wp_source"] = source_tag
    return out


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


def _segment_intersects_zone_cylinder_3d(a: dict[str, float], b: dict[str, float], zone: dict[str, Any], margin_m: float = 0.0) -> bool:
    ax = float(a.get("x", 0.0))
    ay = float(a.get("y", 0.0))
    az = float(a.get("z", 0.0))
    bx = float(b.get("x", 0.0))
    by = float(b.get("y", 0.0))
    bz = float(b.get("z", 0.0))
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = max(0.0, float(zone.get("radius_m", 0.0)) + margin_m)
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))

    dx = bx - ax
    dy = by - ay
    dz = bz - az

    if dz == 0.0:
        if not (z_min <= az <= z_max):
            return False
        t_lo, t_hi = 0.0, 1.0
    else:
        t1 = (z_min - az) / dz
        t2 = (z_max - az) / dz
        t_lo = max(0.0, min(t1, t2))
        t_hi = min(1.0, max(t1, t2))
        if t_lo > t_hi:
            return False

    fx = ax - cx
    fy = ay - cy
    denom = dx * dx + dy * dy
    if denom == 0.0:
        t_star = t_lo
    else:
        t_star = -((fx * dx) + (fy * dy)) / denom
        t_star = max(t_lo, min(t_hi, t_star))
    px = ax + dx * t_star
    py = ay + dy * t_star
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _segment_intersects_zone_xy_footprint(a: dict[str, float], b: dict[str, float], zone: dict[str, Any], margin_m: float = 0.0) -> bool:
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = max(0.0, float(zone.get("radius_m", 0.0)) + margin_m)
    return _segment_intersects_circle_2d(a, b, cx, cy, r)


def _point_inside_zone_with_margin(wp: dict[str, Any], zone: dict[str, Any], margin_m: float) -> bool:
    x = float(wp.get("x", 0.0))
    y = float(wp.get("y", 0.0))
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = float(zone.get("radius_m", 0.0)) + margin_m
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _segment_conflicts_any_zone(a: dict[str, float], b: dict[str, float], zones: list[dict[str, Any]], margin_m: float) -> bool:
    for zone in zones:
        if _segment_intersects_zone_xy_footprint(a, b, zone, margin_m):
            return True
    return False


def _segment_conflicts_any_zone_with_mode(
    a: dict[str, float],
    b: dict[str, float],
    zones: list[dict[str, Any]],
    margin_m: float,
    *,
    conflict_mode: str,
) -> bool:
    for zone in zones:
        if conflict_mode == "xyz_cylinder":
            if _segment_intersects_zone_cylinder_3d(a, b, zone, margin_m):
                return True
        else:
            if _segment_intersects_zone_xy_footprint(a, b, zone, margin_m):
                return True
    return False


def _waypoint_conflicts_zone_with_mode(wp: dict[str, Any], zone: dict[str, Any], *, conflict_mode: str) -> bool:
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    radius = float(zone.get("radius_m", 0.0))
    dx = float(wp.get("x", 0.0)) - cx
    dy = float(wp.get("y", 0.0)) - cy
    inside_xy = (dx * dx + dy * dy) <= radius * radius
    if not inside_xy:
        return False
    if conflict_mode != "xyz_cylinder":
        return True
    z = float(wp.get("z", 0.0))
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))
    return z_min <= z <= z_max


def _waypoint_conflicts_zone_with_policy(wp: dict[str, Any], zone: dict[str, Any], prefs: dict[str, Any]) -> bool:
    mode = str(prefs.get("nfz_conflict_mode", "xyz_cylinder"))
    margin_m = float(prefs.get("margin_m", 0.0))
    if mode == "xy_only":
        return _point_inside_zone_with_margin(wp, zone, margin_m)
    # Finite-height NFZ for balanced/aggressive: 3D cylinder with XY margin and expanded roof clearance.
    return _segment_intersects_zone_cylinder_3d(
        {"x": float(wp.get("x", 0.0)), "y": float(wp.get("y", 0.0)), "z": float(wp.get("z", 0.0))},
        {"x": float(wp.get("x", 0.0)), "y": float(wp.get("y", 0.0)), "z": float(wp.get("z", 0.0))},
        {"cx": zone.get("cx"), "cy": zone.get("cy"), "radius_m": zone.get("radius_m"), "z_min": zone.get("z_min"), "z_max": float(zone.get("z_max", 0.0)) + float(prefs.get("overflight_clearance_m", 0.0))},
        margin_m,
    )


def _segment_conflicts_zone_with_policy(a: dict[str, float], b: dict[str, float], zone: dict[str, Any], prefs: dict[str, Any]) -> bool:
    mode = str(prefs.get("nfz_conflict_mode", "xyz_cylinder"))
    margin_m = float(prefs.get("margin_m", 0.0))
    if mode == "xy_only":
        # Safe mode: infinite-height block over XY footprint.
        return _segment_intersects_zone_xy_footprint(a, b, zone, margin_m)
    # Balanced/aggressive: finite-height NFZ with explicit roof-clearance buffer.
    zone_expanded = dict(zone)
    zone_expanded["z_max"] = float(zone.get("z_max", 0.0)) + float(prefs.get("overflight_clearance_m", 0.0))
    return _segment_intersects_zone_cylinder_3d(a, b, zone_expanded, margin_m)


def _segment_conflicts_any_zone_with_policy(
    a: dict[str, float],
    b: dict[str, float],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> bool:
    for zone in zones:
        if _segment_conflicts_zone_with_policy(a, b, zone, prefs):
            return True
    return False


def _segment_conflict_details(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    margin_m: float,
    *,
    conflict_mode: str = "xy_only",
) -> list[dict[str, Any]]:
    # Backward-compatible wrapper; prefer `_segment_conflict_details_with_policy`.
    prefs: dict[str, Any] = {
        "tracking_xy_buffer_enforced": conflict_mode != "xyz_cylinder",
        "tracking_xy_buffer_margin_m": float(margin_m),
        "margin_m": float(margin_m),
    }
    return _segment_conflict_details_with_policy(route, zones, prefs)


def _segment_conflict_details_with_policy(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(max(0, len(route) - 1)):
        a = route[i]
        b = route[i + 1]
        hit_zones: list[str] = []
        for zone in zones:
            if _segment_conflicts_zone_with_policy(a, b, zone, prefs):
                suffix = "xy_infinite" if str(prefs.get("nfz_conflict_mode", "xyz_cylinder")) == "xy_only" else "xyz_finite"
                hit_zones.append(f"{str(zone.get('zone_id', 'nfz'))}:{suffix}")
        if hit_zones:
            out.append({"segment_index": i, "zone_ids": hit_zones, "from": dict(a), "to": dict(b)})
    return out


def _repair_segment_conflicts(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Final pass: check every adjacent segment and insert detours until clear or budget exhausted."""
    if len(route) < 2:
        return route, [], []
    max_insertions = max(1, int(prefs.get("segment_repair_max_insertions", 10)))
    updated = [dict(w) for w in route]
    insertions: list[dict[str, Any]] = []
    i = 0
    while i < len(updated) - 1 and len(insertions) < max_insertions:
        a = updated[i]
        b = updated[i + 1]
        fixed_here = False
        for zone in zones:
            has_conflict = _segment_conflicts_zone_with_policy(a, b, zone, prefs)
            if not has_conflict:
                continue
            bypass_pts, bypass_meta = _choose_segment_bypass_points(a, b, zone, prefs, zones=zones)
            if not bypass_pts:
                i += 1
                fixed_here = True
                break
            tagged_pts: list[dict[str, Any]] = []
            for p in bypass_pts:
                tagged_pts.append(
                    _mark_agent_inserted_waypoint(
                        p,
                        "utm_replan_overflight_final_fix" if str(bypass_meta.get("strategy")) == "over" else "utm_replan_detour_segment_fix",
                    )
                )
            if _point_near_3d(tagged_pts[0], a) and _point_near_3d(tagged_pts[-1], b):
                i += 1
                fixed_here = True
                break
            for off, p in enumerate(tagged_pts):
                updated.insert(i + 1 + off, p)
            rec = {
                "kind": "segment_detour_final_fix",
                "insert_after_index": i,
                "zone_id": zone.get("zone_id"),
                "strategy": bypass_meta.get("strategy"),
                "detour_points": [dict(p) for p in tagged_pts],
                "cost": dict(bypass_meta),
            }
            insertions.append(rec)
            fixed_here = True
            break
        if not fixed_here:
            i += 1
    remaining = _segment_conflict_details_with_policy(updated, zones, prefs)
    return updated, insertions, remaining


def _segment_length(a: dict[str, float], b: dict[str, float]) -> float:
    dx = float(b.get("x", 0.0)) - float(a.get("x", 0.0))
    dy = float(b.get("y", 0.0)) - float(a.get("y", 0.0))
    dz = float(b.get("z", 0.0)) - float(a.get("z", 0.0))
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _airspace_bounds_for_segment(airspace_segment: str) -> dict[str, tuple[float, float]]:
    if airspace_segment == "sector-A3":
        return {"x": (0.0, 400.0), "y": (0.0, 300.0), "z": (0.0, 120.0)}
    return {"x": (-1e9, 1e9), "y": (-1e9, 1e9), "z": (0.0, 120.0)}


def _waypoint_in_airspace_bounds(wp: dict[str, Any], airspace_segment: str) -> bool:
    bounds = _airspace_bounds_for_segment(airspace_segment)
    x = float(wp.get("x", 0.0))
    y = float(wp.get("y", 0.0))
    z = float(wp.get("z", 0.0))
    return bounds["x"][0] <= x <= bounds["x"][1] and bounds["y"][0] <= y <= bounds["y"][1] and bounds["z"][0] <= z <= bounds["z"][1]


def _segment_within_airspace_bounds(a: dict[str, Any], b: dict[str, Any], airspace_segment: str) -> bool:
    # Axis-aligned box is convex, so endpoints inside implies the straight segment stays inside.
    return _waypoint_in_airspace_bounds(a, airspace_segment) and _waypoint_in_airspace_bounds(b, airspace_segment)


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


def _is_critical_waypoint(route: list[dict[str, Any]], idx: int) -> bool:
    if idx <= 0 or idx >= len(route) - 1:
        return True
    # Preserve all original/user-planned waypoints (including transit) during LoS pruning.
    # Only backend-inserted replan breadcrumbs are candidates for deletion.
    if not _is_replan_generated_waypoint(route[idx]):
        return True
    action = str(route[idx].get("action", "transit"))
    return action != "transit"


def _los_prune_route_with_critical_preservation(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
    *,
    airspace_segment: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Greedy look-ahead pruning that preserves critical/action waypoints.

    Deletes transit breadcrumb points only when the direct line is valid under the active profile
    (NFZ policy + airspace bounds + basic segment length/network heuristics).
    """
    if len(route) <= 2:
        return [dict(w) for w in route], []

    max_seg_m = float(prefs.get("simplification_max_segment_m", 2600.0))
    signal_floor = float(prefs.get("simplification_signal_floor_dbm", -96.0))
    final_path: list[dict[str, Any]] = [dict(route[0])]
    deletions: list[dict[str, Any]] = []
    current_idx = 0

    while current_idx < len(route) - 1:
        # Critical boundary is the next critical waypoint (or end).
        critical_boundary = len(route) - 1
        for j in range(current_idx + 1, len(route)):
            if _is_critical_waypoint(route, j):
                critical_boundary = j
                break

        furthest_safe_idx = current_idx + 1
        # Backtrack from the next critical boundary to find the furthest directly reachable point.
        for j in range(critical_boundary, current_idx, -1):
            a = route[current_idx]
            b = route[j]
            if not _segment_within_airspace_bounds(a, b, airspace_segment):
                continue
            if _segment_length(a, b) > max_seg_m:
                continue
            if _segment_conflicts_any_zone_with_policy(a, b, zones, prefs):
                continue
            if _min_signal_over_segment(a, b) < signal_floor:
                continue
            furthest_safe_idx = j
            break

        if furthest_safe_idx <= current_idx:
            furthest_safe_idx = current_idx + 1

        for k in range(current_idx + 1, furthest_safe_idx):
            if _is_critical_waypoint(route, k):
                continue
            deletions.append(
                {
                    "kind": "waypoint_removed",
                    "index": k,
                    "reason": "los_prune_transit",
                    "waypoint": dict(route[k]),
                }
            )

        final_path.append(dict(route[furthest_safe_idx]))
        current_idx = furthest_safe_idx

    return final_path, deletions


def _iterative_los_prune_inserted_waypoints(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
    *,
    airspace_segment: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    updated = [dict(w) for w in route]
    all_deletions: list[dict[str, Any]] = []
    pass_stats: list[dict[str, Any]] = []
    max_passes = max(1, int(prefs.get("los_prune_max_passes", 4)))
    for pass_index in range(1, max_passes + 1):
        before_count = len(updated)
        updated, deletions = _los_prune_route_with_critical_preservation(
            updated, zones, prefs, airspace_segment=airspace_segment
        )
        deleted_inserted = 0
        for d in deletions:
            wp = d.get("waypoint") if isinstance(d, dict) else None
            if isinstance(wp, dict) and str(wp.get("_wp_origin", "")) == "agent_inserted":
                deleted_inserted += 1
        pass_stats.append(
            {
                "pass_index": pass_index,
                "before_waypoint_count": before_count,
                "after_waypoint_count": len(updated),
                "deleted_count": len(deletions),
                "deleted_agent_inserted_count": deleted_inserted,
            }
        )
        all_deletions.extend(deletions)
        if deleted_inserted == 0:
            break
    return updated, all_deletions, pass_stats


def _trim_nonimpacting_inserted_waypoints(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
    *,
    airspace_segment: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Third-step cleanup: remove inserted transit points that do not affect NFZ avoidance.

    For each inserted waypoint, test whether directly connecting its neighbors remains valid under
    the active NFZ policy and airspace bounds. If valid, delete it and continue iterating.
    """
    if len(route) <= 2:
        return [dict(w) for w in route], [], []

    updated = [dict(w) for w in route]
    deletions: list[dict[str, Any]] = []
    passes: list[dict[str, Any]] = []
    max_passes = max(2, int(prefs.get("los_prune_max_passes", 4)) + 2)

    for pass_index in range(1, max_passes + 1):
        before_count = len(updated)
        deleted_this_pass = 0
        i = 1
        while i < len(updated) - 1:
            cur = updated[i]
            if str(cur.get("_wp_origin", "")) != "agent_inserted":
                i += 1
                continue
            if str(cur.get("action", "transit")) != "transit":
                i += 1
                continue
            a = updated[i - 1]
            b = updated[i + 1]
            if not _segment_within_airspace_bounds(a, b, airspace_segment):
                i += 1
                continue
            if _segment_conflicts_any_zone_with_policy(a, b, zones, prefs):
                i += 1
                continue
            deletions.append(
                {
                    "kind": "waypoint_removed",
                    "index": i,
                    "reason": "inserted_nonimpacting_nfz_trim",
                    "waypoint": dict(cur),
                }
            )
            updated.pop(i)
            deleted_this_pass += 1
            # Re-check at the same index because the route contracted.
        passes.append(
            {
                "pass_index": pass_index,
                "before_waypoint_count": before_count,
                "after_waypoint_count": len(updated),
                "deleted_count": deleted_this_pass,
            }
        )
        if deleted_this_pass == 0:
            break
    return updated, deletions, passes


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
            # Preserve all user-planned waypoints, even if action is transit/hover.
            if not _is_replan_generated_waypoint(cur):
                i += 1
                continue
            # Only transit points may be removed during simplification; all non-transit actions are preserved.
            action = str(cur.get("action", "transit"))
            if action != "transit":
                i += 1
                continue
            if _segment_length(a, b) > float(prefs.get("simplification_max_segment_m", 2600.0)):
                i += 1
                continue
            if _segment_conflicts_any_zone_with_policy(a, b, zones, prefs):
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


def _compress_route_transit_runs(
    route: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reduce crowded replan-generated transit points while preserving NFZ-safe segments.

    Only points tagged as backend-generated detours are candidates for deletion.
    """
    if len(route) <= 2:
        return route, []
    margin_m = float(prefs.get("margin_m", 15.0))
    max_seg_m = max(float(prefs.get("simplification_max_segment_m", 2600.0)), 3800.0)
    relaxed_signal_floor = float(prefs.get("simplification_signal_floor_dbm", -96.0)) - 4.0
    kept: list[dict[str, Any]] = []
    deletions: list[dict[str, Any]] = []
    i = 0
    while i < len(route):
        kept.append(dict(route[i]))
        if i >= len(route) - 1:
            break
        best_j = i + 1
        for j in range(i + 2, len(route)):
            mids = route[i + 1 : j]
            # Never skip past user-planned points.
            if any(not _is_replan_generated_waypoint(m) for m in mids):
                break
            if any(str(m.get("action", "transit")) != "transit" for m in mids):
                break
            a = route[i]
            b = route[j]
            if _segment_length(a, b) > max_seg_m:
                continue
            if _segment_conflicts_any_zone_with_policy(a, b, zones, prefs):
                continue
            if _min_signal_over_segment(a, b) < relaxed_signal_floor:
                continue
            best_j = j
        if best_j > i + 1:
            for k in range(i + 1, best_j):
                deletions.append(
                    {
                        "kind": "waypoint_removed",
                        "index": k,
                        "reason": "transit_run_compression",
                        "waypoint": dict(route[k]),
                    }
                )
        i = best_j
    return kept, deletions


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


def _point_near_3d(a: dict[str, Any], b: dict[str, Any], *, xy_tol: float = 1.0, z_tol: float = 1.0) -> bool:
    return (
        math.hypot(float(a.get("x", 0.0)) - float(b.get("x", 0.0)), float(a.get("y", 0.0)) - float(b.get("y", 0.0))) <= xy_tol
        and abs(float(a.get("z", 0.0)) - float(b.get("z", 0.0))) <= z_tol
    )


def _xy_dist(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.hypot(float(a.get("x", 0.0)) - float(b.get("x", 0.0)), float(a.get("y", 0.0)) - float(b.get("y", 0.0)))


def _safe_xy_graph_nodes_for_segment(
    a: dict[str, Any],
    b: dict[str, Any],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> list[dict[str, float]]:
    """Sparse visibility-style graph nodes for safe XY-around routing (circle boundary samples + endpoints)."""
    margin_m = float(prefs.get("margin_m", 15.0))
    node_offset = margin_m + 1.0  # stay slightly outside the blocked radius
    ax, ay = float(a.get("x", 0.0)), float(a.get("y", 0.0))
    bx, by = float(b.get("x", 0.0)), float(b.get("y", 0.0))
    nodes: list[dict[str, float]] = [{"x": ax, "y": ay}, {"x": bx, "y": by}]

    # Use only zones near the segment corridor to keep graph small.
    minx, maxx = min(ax, bx), max(ax, bx)
    miny, maxy = min(ay, by), max(ay, by)
    corridor_pad = max(40.0, margin_m * 2.0)
    for zone in zones:
        cx = float(zone.get("cx", 0.0))
        cy = float(zone.get("cy", 0.0))
        r = float(zone.get("radius_m", 0.0)) + node_offset
        if cx + r < minx - corridor_pad or cx - r > maxx + corridor_pad or cy + r < miny - corridor_pad or cy - r > maxy + corridor_pad:
            continue
        # 16 samples gives enough quality for current scale without large graph cost.
        for k in range(16):
            theta = (2.0 * math.pi * k) / 16.0
            nodes.append(
                {
                    "x": round(min(400.0, max(0.0, cx + math.cos(theta) * r)), 2),
                    "y": round(min(300.0, max(0.0, cy + math.sin(theta) * r)), 2),
                }
            )
    # De-duplicate coarse-equal points.
    seen: set[tuple[int, int]] = set()
    out: list[dict[str, float]] = []
    for n in nodes:
        key = (int(round(float(n["x"]) * 10)), int(round(float(n["y"]) * 10)))
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def _safe_xy_segment_free(a: dict[str, Any], b: dict[str, Any], zones: list[dict[str, Any]], prefs: dict[str, Any]) -> bool:
    margin_m = float(prefs.get("margin_m", 15.0))
    return not _segment_conflicts_any_zone(a, b, zones, margin_m)


def _safe_xy_a_star_path(
    start: dict[str, float],
    goal: dict[str, float],
    zones: list[dict[str, Any]],
    prefs: dict[str, Any],
) -> list[dict[str, float]]:
    nodes = _safe_xy_graph_nodes_for_segment(start, goal, zones, prefs)
    if len(nodes) < 2:
        return []
    start_idx = 0
    goal_idx = 1

    # Build sparse edges by line-of-sight. O(n^2) is acceptable for small sampled graphs.
    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(len(nodes))]
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a = nodes[i]
            b = nodes[j]
            if not _safe_xy_segment_free(a, b, zones, prefs):
                continue
            d = _xy_dist(a, b)
            neighbors[i].append((j, d))
            neighbors[j].append((i, d))

    pq: list[tuple[float, int]] = []
    heapq.heappush(pq, (0.0, start_idx))
    g_score = {start_idx: 0.0}
    came_from: dict[int, int] = {}
    seen_closed: set[int] = set()
    while pq:
        _, cur = heapq.heappop(pq)
        if cur in seen_closed:
            continue
        seen_closed.add(cur)
        if cur == goal_idx:
            path_idx = [cur]
            while cur in came_from:
                cur = came_from[cur]
                path_idx.append(cur)
            path_idx.reverse()
            return [dict(nodes[i]) for i in path_idx]
        for nxt, w in neighbors[cur]:
            cand = g_score[cur] + w
            if cand >= g_score.get(nxt, float("inf")):
                continue
            g_score[nxt] = cand
            came_from[nxt] = cur
            h = _xy_dist(nodes[nxt], nodes[goal_idx])
            heapq.heappush(pq, (cand + h, nxt))
    return []


def _apply_z_profile_to_xy_path(
    xy_path: list[dict[str, float]],
    a: dict[str, float],
    b: dict[str, float],
) -> list[dict[str, float]]:
    if len(xy_path) <= 2:
        return []
    total = 0.0
    seglens: list[float] = []
    for i in range(1, len(xy_path)):
        d = _xy_dist(xy_path[i - 1], xy_path[i])
        seglens.append(d)
        total += d
    if total <= 0.0:
        return []
    z0 = float(a.get("z", 0.0))
    z1 = float(b.get("z", 0.0))
    acc = 0.0
    out: list[dict[str, float]] = []
    for i in range(1, len(xy_path) - 1):
        acc += seglens[i - 1]
        t = acc / total
        out.append({"x": float(xy_path[i]["x"]), "y": float(xy_path[i]["y"]), "z": round(z0 + (z1 - z0) * t, 2), "action": "transit"})
    return out


def _choose_segment_bypass_points(
    a: dict[str, float],
    b: dict[str, float],
    zone: dict[str, Any],
    prefs: dict[str, Any],
    zones: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Choose the cheaper valid bypass for a conflicting segment: around (XY detour) or over (Z maneuver)."""
    if str(prefs.get("optimization_profile", "balanced")) == "safe":
        xy_path = _safe_xy_a_star_path(a, b, zones or [zone], prefs)
        if xy_path:
            graph_pts = _apply_z_profile_to_xy_path(xy_path, a, b)
            if graph_pts:
                graph_cost = sum(_segment_length(xy_path[i - 1], xy_path[i]) for i in range(1, len(xy_path)))
                return graph_pts, {"strategy": "around_graph_a_star", "cost_around": round(graph_cost, 2), "nodes": len(xy_path)}
    around = _detour_point_for_zone(a, b, zone, prefs)
    around_pts = [around]
    around_cost = _segment_length(a, around) + _segment_length(around, b)
    best_pts = around_pts
    best_meta: dict[str, Any] = {"strategy": "around", "cost_around": round(around_cost, 2)}

    if not bool(prefs.get("allow_overflight", False)):
        return best_pts, best_meta

    z_over = float(zone.get("z_max", 0.0)) + float(prefs.get("overflight_clearance_m", 0.0))
    z_over = min(120.0, max(0.0, z_over))
    # If we cannot get above the protected height+buffer within simulator altitude limits, skip overflight.
    if z_over <= float(zone.get("z_max", 0.0)) + max(0.1, float(prefs.get("overflight_clearance_m", 0.0)) * 0.5):
        return best_pts, best_meta

    p1 = {"x": round(float(a.get("x", 0.0)), 2), "y": round(float(a.get("y", 0.0)), 2), "z": round(z_over, 2), "action": "transit"}
    p2 = {"x": round(float(b.get("x", 0.0)), 2), "y": round(float(b.get("y", 0.0)), 2), "z": round(z_over, 2), "action": "transit"}
    over_segments = [(a, p1), (p1, p2), (p2, b)]
    # Ensure the "over" candidate is actually conflict-free under the current profile semantics.
    if any(_segment_conflicts_zone_with_policy(s0, s1, zone, prefs) for s0, s1 in over_segments):
        return best_pts, best_meta

    climb = max(0.0, z_over - float(a.get("z", 0.0)))
    descend = max(0.0, z_over - float(b.get("z", 0.0)))
    across_xy = math.hypot(float(b.get("x", 0.0)) - float(a.get("x", 0.0)), float(b.get("y", 0.0)) - float(a.get("y", 0.0)))
    wz = max(1.0, float(prefs.get("vertical_cost_weight", 1.5)))
    over_cost = (climb * wz) + across_xy + (descend * wz)
    best_meta["cost_over"] = round(over_cost, 2)
    if over_cost < around_cost:
        best_pts = [p1, p2]
        best_meta = {
            "strategy": "over",
            "cost_over": round(over_cost, 2),
            "cost_around": round(around_cost, 2),
            "z_over": round(z_over, 2),
        }
    return best_pts, best_meta


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
    current_raw = [dict(w) for w in waypoints] if isinstance(waypoints, list) else (list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else [])
    current = [_mark_original_waypoint(w) for w in current_raw]
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
    updated: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    preserved_action_conflicts: list[dict[str, Any]] = []
    replaced_original_waypoints: list[dict[str, Any]] = []

    for idx, wp in enumerate(current):
        next_wp: dict[str, Any] = {"x": float(wp.get("x", 0.0)), "y": float(wp.get("y", 0.0)), "z": float(wp.get("z", 0.0))}
        action = str(wp.get("action", "transit")) if isinstance(wp.get("action"), str) else "transit"
        next_wp["action"] = action
        next_wp["_wp_source"] = str(wp.get("_wp_source", "user_planned") or "user_planned")
        next_wp["_wp_origin"] = str(wp.get("_wp_origin", "original") or "original")
        for zone in zones:
            if _waypoint_conflicts_zone_with_policy(next_wp, zone, prefs):
                prev = dict(next_wp)
                moved = _move_point_outside_zone(next_wp, zone, prefs)
                # Replace the original conflicting waypoint with an agent-inserted mapped waypoint
                # so the UI/DB can distinguish user/original vs replan-generated replacements.
                moved["action"] = action
                next_wp = _mark_agent_inserted_waypoint(
                    moved,
                    "utm_replan_replace_conflict_wp",
                )
                next_wp["_mapped_from_original_index"] = idx
                next_wp["_mapped_from_wp_source"] = str(prev.get("_wp_source", "user_planned") or "user_planned")
                replaced_original_waypoints.append(
                    {
                        "kind": "original_waypoint_replaced_nfz",
                        "index": idx,
                        "zone_id": zone.get("zone_id"),
                        "from": prev,
                        "to": dict(next_wp),
                    }
                )
                changes.append({"index": idx, "zone_id": zone.get("zone_id"), "from": prev, "to": dict(next_wp), "kind": "waypoint_replace"})
        # keep within simulated bounds for sector-A3 if requested
        if airspace_segment == "sector-A3":
            next_wp["x"] = min(400.0, max(0.0, next_wp["x"]))
            next_wp["y"] = min(300.0, max(0.0, next_wp["y"]))
            next_wp["z"] = min(120.0, max(0.0, next_wp["z"]))
        updated.append(next_wp)

    # Second pass: insert detour waypoints when route segments violate the active uncertainty-aware policy.
    segment_insertions: list[dict[str, Any]] = []
    i = 0
    max_insertions = max(1, int(prefs.get("segment_repair_max_insertions", 8)))
    while i < len(updated) - 1 and len(segment_insertions) < max_insertions:
        a = updated[i]
        b = updated[i + 1]
        inserted = False
        for zone in zones:
            if _segment_conflicts_zone_with_policy(a, b, zone, prefs):
                bypass_pts, bypass_meta = _choose_segment_bypass_points(a, b, zone, prefs, zones=zones)
                if not bypass_pts:
                    continue
                tagged_pts: list[dict[str, Any]] = []
                for p in bypass_pts:
                    tagged_pts.append(
                        _mark_agent_inserted_waypoint(
                            p,
                            "utm_replan_overflight" if str(bypass_meta.get("strategy")) == "over" else "utm_replan_detour",
                        )
                    )
                if _point_near_3d(tagged_pts[0], a) and _point_near_3d(tagged_pts[-1], b):
                    continue
                for off, p in enumerate(tagged_pts):
                    updated.insert(i + 1 + off, p)
                rec = {
                    "insert_after_index": i,
                    "zone_id": zone.get("zone_id"),
                    "strategy": bypass_meta.get("strategy"),
                    "detour_points": [dict(p) for p in tagged_pts],
                    "cost": dict(bypass_meta),
                }
                segment_insertions.append(rec)
                changes.append({"kind": "segment_detour", **rec})
                inserted = True
                break
        if not inserted:
            i += 1

    # Third pass: iteratively LoS-prune only agent-inserted breadcrumb points.
    updated, los_prune_deletions, los_prune_passes = _iterative_los_prune_inserted_waypoints(
        updated,
        zones,
        prefs,
        airspace_segment=airspace_segment,
    )
    changes.extend(los_prune_deletions)

    # Fourth pass: small cleanup for any remaining backend-generated transit crowding.
    updated, deletions = _simplify_route_with_constraints(updated, zones, prefs)
    changes.extend(deletions)
    updated, compressed_deletions = _compress_route_transit_runs(updated, zones, prefs)
    changes.extend(compressed_deletions)

    # Final safety pass: check every remaining segment against NFZs (with profile margin) and repair if needed.
    pre_final_segment_conflicts = _segment_conflict_details_with_policy(updated, zones, prefs)
    updated, final_fix_insertions, remaining_segment_conflicts = _repair_segment_conflicts(updated, zones, prefs)
    changes.extend(final_fix_insertions)

    # Third-step cleanup after insert+LoS: delete inserted points that do not affect NFZ clearance.
    updated, inserted_trim_deletions, inserted_trim_passes = _trim_nonimpacting_inserted_waypoints(
        updated,
        zones,
        prefs,
        airspace_segment=airspace_segment,
    )
    changes.extend(inserted_trim_deletions)
    remaining_segment_conflicts = _segment_conflict_details_with_policy(updated, zones, prefs)

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
            "preserved_action_conflicts": preserved_action_conflicts,
            "replaced_original_waypoints": replaced_original_waypoints,
            "segment_insertions": segment_insertions,
            "segment_conflicts_checked": max(0, len(updated) - 1),
            "segment_conflicts_before_final_fix": pre_final_segment_conflicts,
            "segment_conflicts_after_replan": remaining_segment_conflicts,
            "final_segment_clearance_ok": len(remaining_segment_conflicts) == 0,
            "nfz_conflict_mode": str(prefs.get("nfz_conflict_mode", "xyz_cylinder")),
            "allow_overflight": bool(prefs.get("allow_overflight", False)),
            "overflight_clearance_m": float(prefs.get("overflight_clearance_m", 0.0)),
            "vertical_cost_weight": float(prefs.get("vertical_cost_weight", 1.5)),
            "waypoint_deletions": los_prune_deletions + deletions + compressed_deletions + inserted_trim_deletions,
            "los_prune_deletions": los_prune_deletions,
            "los_prune_passes": los_prune_passes,
            "inserted_trim_deletions": inserted_trim_deletions,
            "inserted_trim_passes": inserted_trim_passes,
            "nfz_before": nfz_before,
            "nfz_after": nfz_after,
            "uav": SIM.status(uav_id),
        },
    }

