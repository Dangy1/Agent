from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple


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
    operator_licenses: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "op-001": {"license_class": "BVLOS", "expires_at": "2099-01-01T00:00:00Z", "active": True},
            "op-002": {"license_class": "VLOS", "expires_at": "2099-01-01T00:00:00Z", "active": True},
        }
    )

    def export_state(self) -> Dict[str, Any]:
        return {
            "approvals": dict(self.approvals),
            "weather_by_airspace": dict(self.weather_by_airspace),
            "no_fly_zones": list(self.no_fly_zones),
            "regulations": dict(self.regulations),
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
        if isinstance(state.get("operator_licenses"), dict):
            self.operator_licenses = dict(state["operator_licenses"])

    def set_weather(self, airspace_segment: str, **weather: Any) -> Dict[str, Any]:
        rec = dict(self.weather_by_airspace.get(airspace_segment, {}))
        rec.update(weather)
        self.weather_by_airspace[airspace_segment] = rec
        return rec

    def get_weather(self, airspace_segment: str) -> Dict[str, Any]:
        return dict(self.weather_by_airspace.get(airspace_segment, {}))

    def check_weather(self, airspace_segment: str) -> Dict[str, Any]:
        weather = self.get_weather(airspace_segment)
        max_wind = float(self.regulations.get("max_wind_mps", 12.0))
        min_vis = float(self.regulations.get("min_visibility_km", 3.0))
        max_precip = float(self.regulations.get("allow_precip_mmph_max", 1.0))
        checks = {
            "wind_ok": float(weather.get("wind_mps", 0.0)) <= max_wind,
            "visibility_ok": float(weather.get("visibility_km", 99.0)) >= min_vis,
            "precip_ok": float(weather.get("precip_mmph", 0.0)) <= max_precip,
            "storm_ok": not bool(weather.get("storm_alert", False)),
        }
        return {"airspace_segment": airspace_segment, "weather": weather, "checks": checks, "ok": all(checks.values())}

    def check_no_fly_zones(self, waypoints: List[dict]) -> Dict[str, Any]:
        hits = []
        for zone in self.no_fly_zones:
            if _waypoint_hits_zone(waypoints, zone):
                hits.append({"zone_id": zone.get("zone_id"), "reason": zone.get("reason")})
        return {"ok": len(hits) == 0, "hits": hits, "checked_zones": len(self.no_fly_zones)}

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
        max_duration_min = int(self.regulations.get("max_mission_duration_min", 60))
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
        return {"ok": True, "operator_license_id": operator_license_id, "license": rec, "required_class": required_class}

    def register_operator_license(
        self,
        operator_license_id: str,
        license_class: str = "VLOS",
        expires_at: str = "2099-01-01T00:00:00Z",
        active: bool = True,
    ) -> Dict[str, Any]:
        rec = {
            "license_class": str(license_class).upper(),
            "expires_at": expires_at,
            "active": bool(active),
        }
        self.operator_licenses[operator_license_id] = rec
        return {"operator_license_id": operator_license_id, **rec}

    def check_regulations(self, waypoints: List[dict], requested_speed_mps: float = 12.0) -> Dict[str, Any]:
        bounds = _route_bounds(waypoints)
        span_x = bounds["max_x"] - bounds["min_x"]
        span_y = bounds["max_y"] - bounds["min_y"]
        span = (span_x * span_x + span_y * span_y) ** 0.5
        max_alt = float(self.regulations.get("max_altitude_m", 120.0))
        max_span = float(self.regulations.get("max_route_span_m", 2000.0))
        checks = {
            "geometry_ok": _distance_ok(waypoints),
            "altitude_ok": bounds["max_z"] <= max_alt,
            "route_span_ok": span <= max_span,
            "speed_ok": float(requested_speed_mps) <= 25.0,
        }
        return {"ok": all(checks.values()), "checks": checks, "bounds": bounds, "route_span_m": round(span, 2)}

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
        weather = self.check_weather(airspace_segment)
        nfz = self.check_no_fly_zones(waypoints)
        regs = self.check_regulations(waypoints, requested_speed_mps=requested_speed_mps)
        time_window = self.check_time_window(planned_start_at=planned_start_at, planned_end_at=planned_end_at)
        license_check = self.check_operator_license(operator_license_id=operator_license_id, required_class=required_license_class)
        reasons: List[str] = []
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
        for section in ("weather", "no_fly_zone", "regulations"):
            sec = checks.get(section) if isinstance(checks, dict) else None
            if isinstance(sec, dict) and sec.get("ok") is False:
                return {"ok": False, "error": f"{section}_check_failed", "details": sec}
        return {"ok": True}


UTM_SERVICE = UTMApprovalStore()
