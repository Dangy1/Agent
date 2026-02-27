from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple


DEFAULT_REGULATION_PROFILES: Dict[str, Dict[str, Any]] = {
    "small": {
        "max_altitude_m": 100.0,
        "max_route_span_m": 1200.0,
        "max_wind_mps": 9.0,
        "min_visibility_km": 5.0,
        "allow_precip_mmph_max": 0.5,
        "max_mission_duration_min": 35,
        "max_speed_mps": 18.0,
    },
    "middle": {
        "max_altitude_m": 120.0,
        "max_route_span_m": 2000.0,
        "max_wind_mps": 12.0,
        "min_visibility_km": 3.0,
        "allow_precip_mmph_max": 1.0,
        "max_mission_duration_min": 60,
        "max_speed_mps": 25.0,
    },
    "large": {
        "max_altitude_m": 120.0,
        "max_route_span_m": 3500.0,
        "max_wind_mps": 16.0,
        "min_visibility_km": 2.0,
        "allow_precip_mmph_max": 2.5,
        "max_mission_duration_min": 120,
        "max_speed_mps": 35.0,
    },
}


def _distance_ok(waypoints: List[dict]) -> bool:
    # Simple simulator guardrail: reject absurdly large coordinate jumps.
    if len(waypoints) < 2:
        return True
    try:
        for i in range(1, len(waypoints)):
            a = waypoints[i - 1]
            b = waypoints[i]
            dx = float(b.get("x", 0)) - float(a.get("x", 0))
            dy = float(b.get("y", 0)) - float(a.get("y", 0))
            dz = float(b.get("z", 0)) - float(a.get("z", 0))
            if (dx * dx + dy * dy + dz * dz) ** 0.5 > 5000:
                return False
    except Exception:
        return False
    return True


def _route_bounds(waypoints: List[dict]) -> Dict[str, float]:
    if not waypoints:
        return {"min_x": 0.0, "max_x": 0.0, "min_y": 0.0, "max_y": 0.0, "min_z": 0.0, "max_z": 0.0}
    xs = [float(w.get("x", 0.0)) for w in waypoints]
    ys = [float(w.get("y", 0.0)) for w in waypoints]
    zs = [float(w.get("z", 0.0)) for w in waypoints]
    return {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys), "min_z": min(zs), "max_z": max(zs)}


def _point_in_circle(px: float, py: float, cx: float, cy: float, r: float) -> bool:
    dx = px - cx
    dy = py - cy
    return (dx * dx + dy * dy) <= r * r


def _waypoint_hits_zone(waypoints: List[dict], zone: Dict[str, Any]) -> bool:
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = float(zone.get("radius_m", 0.0))
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))
    for wp in waypoints:
        x = float(wp.get("x", 0.0))
        y = float(wp.get("y", 0.0))
        z = float(wp.get("z", 0.0))
        if z_min <= z <= z_max and _point_in_circle(x, y, cx, cy, r):
            return True
    return False


def _segment_altitude_overlaps_zone(a: Dict[str, Any], b: Dict[str, Any], zone: Dict[str, Any]) -> bool:
    z1 = float(a.get("z", 0.0))
    z2 = float(b.get("z", 0.0))
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))
    return max(min(z1, z2), z_min) <= min(max(z1, z2), z_max)


def _segment_intersects_nfz_cylinder(a: Dict[str, Any], b: Dict[str, Any], zone: Dict[str, Any]) -> bool:
    ax = float(a.get("x", 0.0))
    ay = float(a.get("y", 0.0))
    az = float(a.get("z", 0.0))
    bx = float(b.get("x", 0.0))
    by = float(b.get("y", 0.0))
    bz = float(b.get("z", 0.0))
    cx = float(zone.get("cx", 0.0))
    cy = float(zone.get("cy", 0.0))
    r = max(0.0, float(zone.get("radius_m", 0.0)))
    z_min = float(zone.get("z_min", -1e9))
    z_max = float(zone.get("z_max", 1e9))

    dx = bx - ax
    dy = by - ay
    dz = bz - az

    # Compute the parametric t-range where the segment is within the NFZ altitude slab.
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

    # Minimize XY distance-to-center over the valid altitude interval.
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
    return _point_in_circle(px, py, cx, cy, r)


def _segment_intersects_circle_xy(a: Dict[str, Any], b: Dict[str, Any], cx: float, cy: float, r: float) -> bool:
    ax = float(a.get("x", 0.0))
    ay = float(a.get("y", 0.0))
    bx = float(b.get("x", 0.0))
    by = float(b.get("y", 0.0))
    dx = bx - ax
    dy = by - ay
    if dx == 0.0 and dy == 0.0:
        return _point_in_circle(ax, ay, cx, cy, r)
    t = ((cx - ax) * dx + (cy - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    px = ax + t * dx
    py = ay + t * dy
    return _point_in_circle(px, py, cx, cy, r)


def _utm_decision_feedback(*, approved: bool, reasons: List[str], checks: Dict[str, Any]) -> Dict[str, Any]:
    no_fly = checks.get("no_fly_zone") if isinstance(checks.get("no_fly_zone"), dict) else {}
    wp_conflicts = no_fly.get("waypoint_conflicts") if isinstance(no_fly.get("waypoint_conflicts"), list) else []
    seg_conflicts = no_fly.get("segment_conflicts") if isinstance(no_fly.get("segment_conflicts"), list) else []
    # Keep UI-facing indices zero-based to match simulator waypoint indexing (HM is waypoint 0).
    wp_ids = sorted(
        {int(c.get("waypoint_index")) for c in wp_conflicts if isinstance(c, dict) and isinstance(c.get("waypoint_index"), int)}
    )
    seg_ids = sorted(
        {
            f"{int(c.get('segment_start_index'))}-{int(c.get('segment_end_index'))}"
            for c in seg_conflicts
            if isinstance(c, dict) and isinstance(c.get("segment_start_index"), int) and isinstance(c.get("segment_end_index"), int)
        }
    )

    messages: List[str] = []
    suggestions: List[str] = []
    status = "approved" if approved else "rejected"
    if approved:
        messages.append("UTM approved the flight plan. Launch/transit permissions are valid until expiry.")
        messages.append("No blocking conflicts were found in weather, no-fly-zone, regulation, time-window, or operator-license checks.")
        suggestions.append("Proceed to launch and continue mission monitoring.")
    else:
        messages.append("UTM rejected the flight plan.")
        if "no_fly_zone_conflict" in reasons:
            if wp_ids or seg_ids:
                detail = []
                if wp_ids:
                    detail.append("waypoints " + ", ".join(str(x) for x in wp_ids))
                if seg_ids:
                    detail.append("segments " + ", ".join(seg_ids))
                messages.append("No-fly-zone conflict detected at " + " and ".join(detail) + ".")
            else:
                messages.append("No-fly-zone conflict detected on the submitted route.")
            suggestions.append("Regenerate the route around the no-fly zone and keep waypoint segments outside restricted circles.")
            suggestions.append("Re-run UTM verification after route regeneration.")
        if "route_bounds_violation" in reasons:
            messages.append("Route bounds check failed: one or more waypoints are outside the allowed airspace boundary.")
            suggestions.append("Move out-of-range waypoints back inside the airspace boundary and re-verify.")
        if "weather_restriction" in reasons:
            messages.append("Weather check failed for current airspace conditions.")
            suggestions.append("Adjust schedule or weather constraints before re-verifying.")
        if "regulation_violation" in reasons:
            messages.append("Route violated one or more UTM regulations (geometry/altitude/span/speed).")
            suggestions.append("Reduce altitude/speed or shorten route span, then re-verify.")
        if "time_window_violation" in reasons:
            messages.append("Planned mission time window is invalid or exceeds policy limits.")
            suggestions.append("Update planned start/end times and re-verify.")
        if "operator_license_violation" in reasons:
            messages.append("Operator license check failed (missing/expired/inactive/insufficient class).")
            suggestions.append("Register or update a valid operator license and re-verify.")

    return {
        "status": status,
        "reasons": list(reasons),
        "messages": messages,
        "suggestions": suggestions,
        "nfz_conflict_summary": {
            "waypoints": wp_ids,
            "segments": seg_ids,
            "waypoint_conflicts": wp_conflicts,
            "segment_conflicts": seg_conflicts,
            "counts": no_fly.get("conflict_counts") if isinstance(no_fly, dict) else None,
        },
    }


@dataclass
class UTMApprovalStore:
    approvals: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    weather_by_airspace: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "sector-A3": {"wind_mps": 8.0, "visibility_km": 10.0, "precip_mmph": 0.0, "storm_alert": False},
            "sector-B1": {"wind_mps": 14.0, "visibility_km": 4.0, "precip_mmph": 2.0, "storm_alert": False},
        }
    )
    no_fly_zones: List[Dict[str, Any]] = field(
        default_factory=lambda: [
            {"zone_id": "nfz-1", "cx": 150.0, "cy": 110.0, "radius_m": 35.0, "z_min": 0.0, "z_max": 120.0, "reason": "hospital_helipad"},
            {"zone_id": "nfz-2", "cx": 500.0, "cy": 500.0, "radius_m": 100.0, "z_min": 0.0, "z_max": 500.0, "reason": "restricted_site"},
        ]
    )
    regulations: Dict[str, Any] = field(
        default_factory=lambda: {
            "max_altitude_m": 120.0,
            "max_route_span_m": 2000.0,
            "max_wind_mps": 12.0,
            "min_visibility_km": 3.0,
            "allow_precip_mmph_max": 1.0,
            "max_mission_duration_min": 60,
        }
    )
    regulation_profiles: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_REGULATION_PROFILES.items()}
    )
    operator_licenses: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "op-001": {"license_class": "BVLOS", "uav_size_class": "large", "expires_at": "2099-01-01T00:00:00Z", "active": True},
            "op-002": {"license_class": "VLOS", "uav_size_class": "small", "expires_at": "2099-01-01T00:00:00Z", "active": True},
        }
    )

    def export_state(self) -> Dict[str, Any]:
        return {
            "approvals": dict(self.approvals),
            "weather_by_airspace": dict(self.weather_by_airspace),
            "no_fly_zones": list(self.no_fly_zones),
            "regulations": dict(self.regulations),
            "regulation_profiles": {k: dict(v) for k, v in self.regulation_profiles.items()},
            "operator_licenses": dict(self.operator_licenses),
        }

    def load_state(self, state: Dict[str, Any] | None) -> None:
        if not isinstance(state, dict):
            return
        if isinstance(state.get("approvals"), dict):
            self.approvals = dict(state["approvals"])
        if isinstance(state.get("weather_by_airspace"), dict):
            self.weather_by_airspace = dict(state["weather_by_airspace"])
        if isinstance(state.get("no_fly_zones"), list):
            self.no_fly_zones = [dict(z) for z in state["no_fly_zones"] if isinstance(z, dict)]
        if isinstance(state.get("regulations"), dict):
            self.regulations = dict(state["regulations"])
        if isinstance(state.get("regulation_profiles"), dict):
            parsed_profiles: Dict[str, Dict[str, Any]] = {}
            for key, value in state["regulation_profiles"].items():
                if isinstance(value, dict):
                    parsed_profiles[str(key)] = dict(value)
            if parsed_profiles:
                self.regulation_profiles = parsed_profiles
        if isinstance(state.get("operator_licenses"), dict):
            self.operator_licenses = dict(state["operator_licenses"])

    def set_weather(self, airspace_segment: str, **weather: Any) -> Dict[str, Any]:
        rec = dict(self.weather_by_airspace.get(airspace_segment, {}))
        rec.update(weather)
        self.weather_by_airspace[airspace_segment] = rec
        return rec

    def get_weather(self, airspace_segment: str) -> Dict[str, Any]:
        return dict(self.weather_by_airspace.get(airspace_segment, {}))

    def _normalize_uav_size_class(self, value: Any) -> str:
        t = str(value or "").strip().lower()
        aliases = {
            "small": "small",
            "s": "small",
            "micro": "small",
            "middle": "middle",
            "medium": "middle",
            "mid": "middle",
            "m": "middle",
            "large": "large",
            "heavy": "large",
            "l": "large",
        }
        return aliases.get(t, "middle")

    def _license_size_class(self, operator_license_id: str | None = None) -> str:
        if not operator_license_id:
            return "middle"
        rec = self.operator_licenses.get(operator_license_id)
        if not isinstance(rec, dict):
            return "middle"
        return self._normalize_uav_size_class(rec.get("uav_size_class", rec.get("uav_type")))

    def effective_regulations(self, operator_license_id: str | None = None) -> Dict[str, Any]:
        size_class = self._license_size_class(operator_license_id)
        base = dict(self.regulations)
        base.update(dict(self.regulation_profiles.get(size_class, self.regulation_profiles.get("middle", {}))))
        base["uav_size_class"] = size_class
        base["operator_license_id"] = operator_license_id
        return base

    def check_weather(self, airspace_segment: str, operator_license_id: str | None = None) -> Dict[str, Any]:
        weather = self.get_weather(airspace_segment)
        effective = self.effective_regulations(operator_license_id)
        max_wind = float(effective.get("max_wind_mps", 12.0))
        min_vis = float(effective.get("min_visibility_km", 3.0))
        max_precip = float(effective.get("allow_precip_mmph_max", 1.0))
        checks = {
            "wind_ok": float(weather.get("wind_mps", 0.0)) <= max_wind,
            "visibility_ok": float(weather.get("visibility_km", 99.0)) >= min_vis,
            "precip_ok": float(weather.get("precip_mmph", 0.0)) <= max_precip,
            "storm_ok": not bool(weather.get("storm_alert", False)),
        }
        return {
            "airspace_segment": airspace_segment,
            "weather": weather,
            "checks": checks,
            "ok": all(checks.values()),
            "limits": {
                "max_wind_mps": max_wind,
                "min_visibility_km": min_vis,
                "allow_precip_mmph_max": max_precip,
            },
            "uav_size_class": str(effective.get("uav_size_class", "middle")),
            "operator_license_id": operator_license_id,
        }

    def check_route_bounds(self, airspace_segment: str, waypoints: List[dict]) -> Dict[str, Any]:
        bounds = {"sector-A3": {"x": [0.0, 400.0], "y": [0.0, 300.0], "z": [0.0, 120.0]}}
        seg = bounds.get(airspace_segment, {"x": [-1e9, 1e9], "y": [-1e9, 1e9], "z": [0.0, 120.0]})
        out_of_bounds: List[Dict[str, Any]] = []
        for i, wp in enumerate(waypoints):
            x = float(wp.get("x", 0.0))
            y = float(wp.get("y", 0.0))
            z = float(wp.get("z", 0.0))
            if not (seg["x"][0] <= x <= seg["x"][1] and seg["y"][0] <= y <= seg["y"][1] and seg["z"][0] <= z <= seg["z"][1]):
                out_of_bounds.append({"index": i, "wp": {"x": x, "y": y, "z": z}})
        return {
            "airspace_segment": airspace_segment,
            "ok": len(out_of_bounds) == 0,
            "bounds_ok": len(out_of_bounds) == 0,
            "geofence_ok": len(out_of_bounds) == 0,
            "bounds": seg,
            "out_of_bounds": out_of_bounds,
        }

    def check_no_fly_zones(self, waypoints: List[dict]) -> Dict[str, Any]:
        hits = []
        waypoint_conflicts: List[Dict[str, Any]] = []
        segment_conflicts: List[Dict[str, Any]] = []
        for zone in self.no_fly_zones:
            zone_hit = False
            cx = float(zone.get("cx", 0.0))
            cy = float(zone.get("cy", 0.0))
            r = float(zone.get("radius_m", 0.0))
            z_min = float(zone.get("z_min", -1e9))
            z_max = float(zone.get("z_max", 1e9))
            zid = str(zone.get("zone_id", "nfz"))
            reason = str(zone.get("reason", "restricted"))

            for i, wp in enumerate(waypoints):
                x = float(wp.get("x", 0.0))
                y = float(wp.get("y", 0.0))
                z = float(wp.get("z", 0.0))
                if z_min <= z <= z_max and _point_in_circle(x, y, cx, cy, r):
                    zone_hit = True
                    waypoint_conflicts.append(
                        {
                            "zone_id": zid,
                            "reason": reason,
                            "waypoint_index": i,
                            "waypoint": {"x": x, "y": y, "z": z},
                            "conflict": "waypoint_inside_nfz",
                        }
                    )

            for i in range(1, len(waypoints)):
                a = waypoints[i - 1]
                b = waypoints[i]
                if _segment_intersects_nfz_cylinder(a, b, zone):
                    zone_hit = True
                    segment_conflicts.append(
                        {
                            "zone_id": zid,
                            "reason": reason,
                            "segment_start_index": i - 1,
                            "segment_end_index": i,
                            "a": {"x": float(a.get("x", 0.0)), "y": float(a.get("y", 0.0)), "z": float(a.get("z", 0.0))},
                            "b": {"x": float(b.get("x", 0.0)), "y": float(b.get("y", 0.0)), "z": float(b.get("z", 0.0))},
                            "conflict": "segment_crosses_nfz_3d",
                        }
                    )

            if zone_hit:
                hits.append({"zone_id": zone.get("zone_id"), "reason": zone.get("reason")})
        return {
            "ok": len(hits) == 0,
            "hits": hits,
            "checked_zones": len(self.no_fly_zones),
            "waypoint_conflicts": waypoint_conflicts,
            "segment_conflicts": segment_conflicts,
            "conflict_counts": {
                "zones": len(hits),
                "waypoints": len(waypoint_conflicts),
                "segments": len(segment_conflicts),
            },
        }

    def add_no_fly_zone(
        self,
        *,
        cx: float,
        cy: float,
        radius_m: float,
        z_min: float = 0.0,
        z_max: float = 120.0,
        reason: str = "operator_defined",
        zone_id: str | None = None,
    ) -> Dict[str, Any]:
        zid = zone_id or f"nfz-{len(self.no_fly_zones) + 1}"
        rec = {
            "zone_id": str(zid),
            "cx": float(cx),
            "cy": float(cy),
            "radius_m": float(radius_m),
            "z_min": float(z_min),
            "z_max": float(z_max),
            "reason": str(reason),
        }
        self.no_fly_zones = [z for z in self.no_fly_zones if str(z.get("zone_id")) != rec["zone_id"]]
        self.no_fly_zones.append(rec)
        return rec

    def check_time_window(
        self,
        planned_start_at: str | None = None,
        planned_end_at: str | None = None,
        operator_license_id: str | None = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        errors: List[str] = []
        start_dt = None
        end_dt = None
        if planned_start_at:
            try:
                start_dt = datetime.fromisoformat(str(planned_start_at).replace("Z", "+00:00"))
            except Exception:
                errors.append("invalid_planned_start_at")
        if planned_end_at:
            try:
                end_dt = datetime.fromisoformat(str(planned_end_at).replace("Z", "+00:00"))
            except Exception:
                errors.append("invalid_planned_end_at")
        if start_dt and end_dt and end_dt <= start_dt:
            errors.append("end_before_start")
        effective = self.effective_regulations(operator_license_id)
        max_duration_min = int(effective.get("max_mission_duration_min", 60))
        if start_dt and end_dt:
            dur_min = (end_dt - start_dt).total_seconds() / 60.0
            if dur_min > max_duration_min:
                errors.append("duration_exceeds_limit")
        if start_dt and start_dt < now - timedelta(minutes=1):
            errors.append("start_in_past")
        return {
            "ok": len(errors) == 0,
            "errors": errors,
            "planned_start_at": planned_start_at,
            "planned_end_at": planned_end_at,
            "max_mission_duration_min": max_duration_min,
            "uav_size_class": str(effective.get("uav_size_class", "middle")),
            "operator_license_id": operator_license_id,
        }

    def check_operator_license(
        self,
        operator_license_id: str | None = None,
        required_class: str = "VLOS",
    ) -> Dict[str, Any]:
        if not operator_license_id:
            return {"ok": False, "error": "missing_operator_license_id"}
        rec = self.operator_licenses.get(operator_license_id)
        if not rec:
            return {"ok": False, "error": "license_not_found", "operator_license_id": operator_license_id}
        if not rec.get("active", False):
            return {"ok": False, "error": "license_inactive", "operator_license_id": operator_license_id, "license": rec}
        expires = str(rec.get("expires_at", "") or "")
        try:
            if expires:
                dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if dt < datetime.now(timezone.utc):
                    return {"ok": False, "error": "license_expired", "operator_license_id": operator_license_id, "license": rec}
        except Exception:
            return {"ok": False, "error": "license_expiry_parse_failed", "operator_license_id": operator_license_id, "license": rec}
        lic_class = str(rec.get("license_class", ""))
        allowed = {"VLOS": {"VLOS", "BVLOS"}, "BVLOS": {"BVLOS"}}
        if lic_class not in allowed.get(required_class, {required_class}):
            return {
                "ok": False,
                "error": "license_class_insufficient",
                "operator_license_id": operator_license_id,
                "license": rec,
                "required_class": required_class,
            }
        normalized = dict(rec)
        normalized["uav_size_class"] = self._normalize_uav_size_class(rec.get("uav_size_class", rec.get("uav_type")))
        return {
            "ok": True,
            "operator_license_id": operator_license_id,
            "license": normalized,
            "required_class": required_class,
            "uav_size_class": normalized["uav_size_class"],
            "effective_regulations": self.effective_regulations(operator_license_id),
        }

    def register_operator_license(
        self,
        operator_license_id: str,
        license_class: str = "VLOS",
        uav_size_class: str = "middle",
        expires_at: str = "2099-01-01T00:00:00Z",
        active: bool = True,
    ) -> Dict[str, Any]:
        rec = {
            "license_class": str(license_class).upper(),
            "uav_size_class": self._normalize_uav_size_class(uav_size_class),
            "expires_at": expires_at,
            "active": bool(active),
        }
        self.operator_licenses[operator_license_id] = rec
        return {"operator_license_id": operator_license_id, **rec}

    def check_regulations(self, waypoints: List[dict], requested_speed_mps: float = 12.0, operator_license_id: str | None = None) -> Dict[str, Any]:
        bounds = _route_bounds(waypoints)
        span_x = bounds["max_x"] - bounds["min_x"]
        span_y = bounds["max_y"] - bounds["min_y"]
        span = (span_x * span_x + span_y * span_y) ** 0.5
        effective = self.effective_regulations(operator_license_id)
        max_alt = float(effective.get("max_altitude_m", 120.0))
        max_span = float(effective.get("max_route_span_m", 2000.0))
        max_speed = float(effective.get("max_speed_mps", 25.0))
        checks = {
            "geometry_ok": _distance_ok(waypoints),
            "altitude_ok": bounds["max_z"] <= max_alt,
            "route_span_ok": span <= max_span,
            "speed_ok": float(requested_speed_mps) <= max_speed,
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "bounds": bounds,
            "route_span_m": round(span, 2),
            "limits": {
                "max_altitude_m": max_alt,
                "max_route_span_m": max_span,
                "max_speed_mps": max_speed,
            },
            "uav_size_class": str(effective.get("uav_size_class", "middle")),
            "operator_license_id": operator_license_id,
        }

    def _compose_verdict(
        self,
        *,
        uav_id: str,
        airspace_segment: str,
        route_id: str,
        waypoints: List[dict],
        requested_minutes: int,
        requested_speed_mps: float,
        planned_start_at: str | None = None,
        planned_end_at: str | None = None,
        operator_license_id: str | None = None,
        required_license_class: str = "VLOS",
    ) -> Tuple[bool, Dict[str, Any], List[str]]:
        weather = self.check_weather(airspace_segment, operator_license_id=operator_license_id)
        route_bounds = self.check_route_bounds(airspace_segment, waypoints)
        nfz = self.check_no_fly_zones(waypoints)
        regs = self.check_regulations(waypoints, requested_speed_mps=requested_speed_mps, operator_license_id=operator_license_id)
        time_window = self.check_time_window(
            planned_start_at=planned_start_at,
            planned_end_at=planned_end_at,
            operator_license_id=operator_license_id,
        )
        license_check = self.check_operator_license(operator_license_id=operator_license_id, required_class=required_license_class)
        reasons: List[str] = []
        if not route_bounds["ok"]:
            reasons.append("route_bounds_violation")
        if not weather["ok"]:
            reasons.append("weather_restriction")
        if not nfz["ok"]:
            reasons.append("no_fly_zone_conflict")
        if not regs["ok"]:
            reasons.append("regulation_violation")
        if not time_window["ok"]:
            reasons.append("time_window_violation")
        if not license_check["ok"]:
            reasons.append("operator_license_violation")
        checks = {
            "route_bounds": route_bounds,
            "weather": weather,
            "no_fly_zone": nfz,
            "regulations": regs,
            "time_window": time_window,
            "operator_license": license_check,
        }
        return (len(reasons) == 0), checks, reasons

    def verify_flight_plan(
        self,
        *,
        uav_id: str,
        airspace_segment: str,
        route_id: str = "route-1",
        waypoints: List[dict] | None = None,
        requested_minutes: int = 30,
        requested_speed_mps: float = 12.0,
        planned_start_at: str | None = None,
        planned_end_at: str | None = None,
        operator_license_id: str | None = None,
        required_license_class: str = "VLOS",
    ) -> Dict[str, Any]:
        waypoints = waypoints or []
        approved, checks, reasons = self._compose_verdict(
            uav_id=uav_id,
            airspace_segment=airspace_segment,
            route_id=route_id,
            waypoints=waypoints,
            requested_minutes=requested_minutes,
            requested_speed_mps=requested_speed_mps,
            planned_start_at=planned_start_at,
            planned_end_at=planned_end_at,
            operator_license_id=operator_license_id,
            required_license_class=required_license_class,
        )
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=max(1, requested_minutes))).isoformat().replace("+00:00", "Z")
        approval_id = f"utm-{uav_id}-{route_id}"
        rec = {
            "approval_id": approval_id,
            "issuer": "UTM",
            "uav_id": uav_id,
            "route_id": route_id,
            "airspace_segment": airspace_segment,
            "approved": approved,
            "permissions": ["launch", "transit", "altitude_change"] if approved else [],
            "expires_at": expires_at,
            "signature_verified": approved,
            "reason": "ok" if approved else ",".join(reasons),
            "checks": checks,
            "decision": _utm_decision_feedback(approved=approved, reasons=reasons, checks=checks),
            "scope": {
                "uav_id": uav_id,
                "airspace": airspace_segment,
                "route_id": route_id,
                "time_window": [now.isoformat().replace("+00:00", "Z"), expires_at],
            },
            "operator_license_id": operator_license_id,
        }
        self.approvals[f"{uav_id}:{route_id}"] = rec
        return rec

    def get_approval(self, uav_id: str, route_id: str) -> Dict[str, Any] | None:
        return self.approvals.get(f"{uav_id}:{route_id}")

    def validate_approval_for_launch(self, approval: Dict[str, Any] | None, *, uav_id: str, route_id: str) -> Dict[str, Any]:
        if not approval:
            return {"ok": False, "error": "missing_approval"}
        if str(approval.get("uav_id")) != uav_id or str(approval.get("route_id")) != route_id:
            return {"ok": False, "error": "approval_scope_mismatch"}
        if not approval.get("approved") or not approval.get("signature_verified"):
            return {"ok": False, "error": "approval_not_valid"}
        expires_at = str(approval.get("expires_at", "") or "")
        try:
            if expires_at:
                dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if dt < datetime.now(timezone.utc):
                    return {"ok": False, "error": "approval_expired"}
        except Exception:
            return {"ok": False, "error": "approval_expiry_parse_failed"}
        checks = approval.get("checks") or {}
        for section in ("route_bounds", "weather", "no_fly_zone", "regulations"):
            sec = checks.get(section) if isinstance(checks, dict) else None
            if isinstance(sec, dict) and sec.get("ok") is False:
                return {"ok": False, "error": f"{section}_check_failed", "details": sec}
        return {"ok": True}


UTM_SERVICE = UTMApprovalStore()
