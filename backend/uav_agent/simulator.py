from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from typing import Any, Dict, List

from geo_utils import (
    distance_3d_m,
    extract_lon_lat_alt,
    haversine_m,
    is_valid_lon_lat,
    lon_lat_from_local_xy_m,
    normalize_waypoints,
    point_with_aliases,
)

DEFAULT_ROUTE = [
    {"lon": 24.8164, "lat": 60.1808, "altM": 40.0},
    {"lon": 24.8268, "lat": 60.1860, "altM": 70.0},
    {"lon": 24.8385, "lat": 60.1915, "altM": 65.0},
    {"lon": 24.8502, "lat": 60.1968, "altM": 55.0},
]
OTANIEMI_CENTER_LON = float(os.getenv("UAV_OTANIEMI_CENTER_LON", "24.8286") or 24.8286)
OTANIEMI_CENTER_LAT = float(os.getenv("UAV_OTANIEMI_CENTER_LAT", "60.1866") or 60.1866)
LEGACY_LOCAL_MAX_X_M = float(os.getenv("UAV_LEGACY_LOCAL_MAX_X_M", "2000") or 2000)
LEGACY_LOCAL_MAX_Y_M = float(os.getenv("UAV_LEGACY_LOCAL_MAX_Y_M", "2000") or 2000)
MAP_RELEVANCE_RADIUS_M = float(os.getenv("UAV_MAP_RELEVANCE_RADIUS_M", "20000") or 20000)


@dataclass
class SimUAV:
    uav_id: str
    route_id: str = "route-1"
    waypoints: List[dict] = field(default_factory=lambda: normalize_waypoints(DEFAULT_ROUTE))
    waypoint_index: int = 0
    segment_progress: float = 0.0
    position: Dict[str, float] = field(default_factory=lambda: point_with_aliases(DEFAULT_ROUTE[0]))
    velocity_mps: float = 12.0
    battery_pct: float = 100.0
    distance_travelled_m: float = 0.0
    route_progress_pct: float = 0.0
    flight_phase: str = "IDLE"
    armed: bool = False
    active: bool = False
    last_update_ts: str = ""
    utm_approval: Dict[str, Any] | None = None
    utm_geofence_result: Dict[str, Any] | None = None
    data_source: str = "simulated"
    data_source_meta: Dict[str, Any] | None = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "uav_id": self.uav_id,
            "route_id": self.route_id,
            "waypoint_index": self.waypoint_index,
            "segment_progress": round(max(0.0, min(1.0, float(self.segment_progress))), 4),
            "waypoints_total": len(self.waypoints),
            "waypoints": [point_with_aliases(w) for w in self.waypoints],
            "position": point_with_aliases(self.position),
            "velocity_mps": self.velocity_mps,
            "battery_pct": round(self.battery_pct, 2),
            "distance_travelled_m": round(self.distance_travelled_m, 3),
            "route_progress_pct": round(max(0.0, min(100.0, float(self.route_progress_pct))), 3),
            "flight_phase": self.flight_phase,
            "armed": self.armed,
            "active": self.active,
            "last_update_ts": self.last_update_ts,
            "utm_approval": self.utm_approval,
            "utm_geofence_result": self.utm_geofence_result,
            "data_source": self.data_source,
            "data_source_meta": dict(self.data_source_meta) if isinstance(self.data_source_meta, dict) else None,
        }


class UAVSimulator:
    def __init__(self) -> None:
        self._fleet: Dict[str, SimUAV] = {}

    @staticmethod
    def _prefer_otaniemi_bounds(source: str) -> bool:
        mode = str(source or "").strip().lower()
        return mode in {"", "sim", "simulated", "demo", "synthetic", "test"}

    def _coerce_lon_lat(
        self,
        lon: float,
        lat: float,
        *,
        ref_lon: float | None = None,
        ref_lat: float | None = None,
        force_within_map: bool = False,
    ) -> tuple[float, float]:
        x = float(lon)
        y = float(lat)
        anchor_lon = float(ref_lon if ref_lon is not None else OTANIEMI_CENTER_LON)
        anchor_lat = float(ref_lat if ref_lat is not None else OTANIEMI_CENTER_LAT)
        looks_local_xy = 0.0 <= x <= LEGACY_LOCAL_MAX_X_M and 0.0 <= y <= LEGACY_LOCAL_MAX_Y_M
        if is_valid_lon_lat(x, y):
            if haversine_m(x, y, anchor_lon, anchor_lat) <= MAP_RELEVANCE_RADIUS_M:
                return x, y
            if looks_local_xy:
                return lon_lat_from_local_xy_m(x, y, ref_lon=anchor_lon, ref_lat=anchor_lat)
            if force_within_map:
                return anchor_lon, anchor_lat
            return x, y
        if looks_local_xy:
            return lon_lat_from_local_xy_m(x, y, ref_lon=anchor_lon, ref_lat=anchor_lat)
        return anchor_lon, anchor_lat

    def _sanitize_waypoints(self, waypoints: List[dict], *, force_within_map: bool) -> List[dict]:
        normalized = normalize_waypoints(waypoints)
        if not normalized:
            return normalize_waypoints(DEFAULT_ROUTE)
        ref_lon = OTANIEMI_CENTER_LON
        ref_lat = OTANIEMI_CENTER_LAT
        out: List[dict] = []
        for row in normalized:
            lon, lat, alt_m = extract_lon_lat_alt(row)
            lon, lat = self._coerce_lon_lat(
                lon,
                lat,
                ref_lon=ref_lon,
                ref_lat=ref_lat,
                force_within_map=force_within_map,
            )
            clean = dict(row)
            clean["lon"] = float(lon)
            clean["lat"] = float(lat)
            clean["altM"] = max(0.0, float(alt_m))
            out.append(point_with_aliases(clean))
            ref_lon = float(lon)
            ref_lat = float(lat)
        return out or normalize_waypoints(DEFAULT_ROUTE)

    @staticmethod
    def _dist(a: Dict[str, Any], b: Dict[str, Any]) -> float:
        return distance_3d_m(a, b)

    def _route_total_length(self, u: SimUAV) -> float:
        if len(u.waypoints) < 2:
            return 0.0
        total = 0.0
        for i in range(len(u.waypoints) - 1):
            total += self._dist(u.waypoints[i], u.waypoints[i + 1])
        return total

    def _distance_along_route(self, u: SimUAV) -> float:
        if len(u.waypoints) < 2:
            return 0.0
        idx = max(0, min(int(u.waypoint_index), len(u.waypoints) - 1))
        along = 0.0
        for i in range(min(idx, len(u.waypoints) - 1)):
            along += self._dist(u.waypoints[i], u.waypoints[i + 1])
        if idx < len(u.waypoints) - 1:
            seg = self._dist(u.waypoints[idx], u.waypoints[idx + 1])
            along += max(0.0, min(1.0, float(u.segment_progress))) * seg
        return along

    def _refresh_route_progress(self, u: SimUAV) -> None:
        total = self._route_total_length(u)
        if total <= 1e-9:
            u.route_progress_pct = 0.0
            return
        along = self._distance_along_route(u)
        u.route_progress_pct = max(0.0, min(100.0, 100.0 * along / total))

    def _project_position_to_route(self, u: SimUAV, pos: Dict[str, float]) -> tuple[int, float]:
        """Project an arbitrary position to the nearest route segment."""
        if len(u.waypoints) < 2:
            return 0, 0.0
        px, py, pz = float(pos.get("x", 0.0)), float(pos.get("y", 0.0)), float(pos.get("z", 0.0))
        best_i = 0
        best_t = 0.0
        best_d2 = float("inf")
        for i in range(len(u.waypoints) - 1):
            a = u.waypoints[i]
            b = u.waypoints[i + 1]
            ax, ay, az = float(a.get("x", 0.0)), float(a.get("y", 0.0)), float(a.get("z", 0.0))
            bx, by, bz = float(b.get("x", 0.0)), float(b.get("y", 0.0)), float(b.get("z", 0.0))
            vx, vy, vz = bx - ax, by - ay, bz - az
            seg2 = vx * vx + vy * vy + vz * vz
            if seg2 <= 1e-9:
                t = 0.0
            else:
                t = ((px - ax) * vx + (py - ay) * vy + (pz - az) * vz) / seg2
                t = max(0.0, min(1.0, t))
            qx, qy, qz = ax + vx * t, ay + vy * t, az + vz * t
            dx, dy, dz = px - qx, py - qy, pz - qz
            d2 = dx * dx + dy * dy + dz * dz
            if d2 < best_d2:
                best_d2 = d2
                best_i = i
                best_t = t
        return best_i, best_t

    def _reset_progress_for_new_mission(self, u: SimUAV) -> None:
        if not u.waypoints:
            return
        first = u.waypoints[0]
        u.waypoint_index = 0
        u.segment_progress = 0.0
        u.position = point_with_aliases(first)
        u.distance_travelled_m = 0.0
        self._refresh_route_progress(u)

    def get_or_create(self, uav_id: str) -> SimUAV:
        if uav_id not in self._fleet:
            self._fleet[uav_id] = SimUAV(uav_id=uav_id)
        return self._fleet[uav_id]

    def plan_route(self, uav_id: str, route_id: str, waypoints: List[dict] | None = None) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.route_id = route_id
        u.waypoints = normalize_waypoints(DEFAULT_ROUTE if waypoints is None else waypoints)
        u.waypoint_index = 0
        first = u.waypoints[0] if u.waypoints else point_with_aliases({})
        u.position = point_with_aliases(first)
        u.segment_progress = 0.0
        u.distance_travelled_m = 0.0
        u.flight_phase = "PLANNED"
        u.active = False
        u.armed = False
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def set_approval(self, uav_id: str, approval: Dict[str, Any]) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.utm_approval = dict(approval)
        self._mark_update(u)
        return u.snapshot()

    def set_geofence_result(self, uav_id: str, geofence_result: Dict[str, Any]) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.utm_geofence_result = dict(geofence_result)
        self._mark_update(u)
        return u.snapshot()

    def launch(self, uav_id: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        if not (u.utm_approval and u.utm_approval.get("approved") and u.utm_approval.get("signature_verified")):
            raise ValueError("UTM approval required before launch")
        # Normal control behavior: if previous mission finished at final waypoint,
        # launch starts a new sortie from waypoint 0 for simulated vehicles.
        if str(u.data_source or "simulated").strip().lower() == "simulated" and (
            len(u.waypoints) >= 1 and u.waypoint_index >= len(u.waypoints) - 1
        ):
            self._reset_progress_for_new_mission(u)
        u.armed = True
        u.active = True
        u.flight_phase = "TAKEOFF" if u.waypoint_index == 0 else "MISSION"
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def step(self, uav_id: str, ticks: int = 1) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        ticks = max(1, int(ticks))
        for _ in range(ticks):
            if not u.active:
                break
            if len(u.waypoints) < 2 or u.waypoint_index >= len(u.waypoints) - 1:
                u.flight_phase = "LOITER"
                u.active = False
                break
            remaining_move_m = max(0.1, float(u.velocity_mps))
            moved_this_tick = 0.0
            while remaining_move_m > 1e-9 and u.active:
                if u.waypoint_index >= len(u.waypoints) - 1:
                    u.flight_phase = "ARRIVAL"
                    u.active = False
                    break
                start_wp = u.waypoints[u.waypoint_index]
                end_wp = u.waypoints[u.waypoint_index + 1]
                seg_len = self._dist(start_wp, end_wp)
                if seg_len <= 1e-9:
                    u.waypoint_index += 1
                    u.segment_progress = 0.0
                    u.position = point_with_aliases(end_wp)
                    continue
                dist_on_seg = max(0.0, min(1.0, float(u.segment_progress))) * seg_len
                remaining_seg = max(0.0, seg_len - dist_on_seg)
                move = min(remaining_move_m, remaining_seg)
                moved_this_tick += move
                remaining_move_m -= move
                dist_on_seg += move
                if dist_on_seg >= seg_len - 1e-9:
                    u.waypoint_index += 1
                    u.segment_progress = 0.0
                    u.position = point_with_aliases(end_wp)
                    if u.waypoint_index >= len(u.waypoints) - 1:
                        u.flight_phase = "ARRIVAL"
                        u.active = False
                    else:
                        u.flight_phase = "MISSION"
                else:
                    u.segment_progress = max(0.0, min(1.0, dist_on_seg / seg_len))
                    sx, sy, sz = float(start_wp.get("x", 0.0)), float(start_wp.get("y", 0.0)), float(start_wp.get("z", 0.0))
                    ex, ey, ez = float(end_wp.get("x", 0.0)), float(end_wp.get("y", 0.0)), float(end_wp.get("z", 0.0))
                    t = u.segment_progress
                    u.position = point_with_aliases(
                        {
                            "x": sx + (ex - sx) * t,
                            "y": sy + (ey - sy) * t,
                            "z": sz + (ez - sz) * t,
                        }
                    )
                    u.flight_phase = "MISSION" if u.waypoint_index > 0 else "TAKEOFF"
            u.distance_travelled_m += moved_this_tick
            u.battery_pct = max(0.0, u.battery_pct - 0.8)
            if u.battery_pct < 15.0:
                u.flight_phase = "LOW_BATTERY"
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def hold(self, uav_id: str, reason: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.active = False
        u.flight_phase = "HOLD"
        self._refresh_route_progress(u)
        self._mark_update(u)
        snap = u.snapshot()
        snap["hold_reason"] = reason
        return snap

    def resume(self, uav_id: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        if not u.armed:
            raise ValueError("Cannot resume: UAV is not armed")
        if u.flight_phase in {"LAND", "IDLE"}:
            raise ValueError(f"Cannot resume from phase {u.flight_phase}")
        if u.waypoint_index >= len(u.waypoints) - 1:
            u.active = False
            u.flight_phase = "LOITER"
        else:
            u.active = True
            u.flight_phase = "MISSION" if u.waypoint_index > 0 else "TAKEOFF"
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def rth(self, uav_id: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.active = False
        u.flight_phase = "RTH"
        if u.waypoints:
            home = u.waypoints[0]
            u.position = point_with_aliases(home)
            u.waypoint_index = 0
            u.segment_progress = 0.0
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def land(self, uav_id: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.active = False
        u.armed = False
        u.flight_phase = "LAND"
        u.position["z"] = 0.0
        u.position["altM"] = 0.0
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def status(self, uav_id: str) -> Dict[str, Any]:
        return self.get_or_create(uav_id).snapshot()

    def status_if_exists(self, uav_id: str) -> Dict[str, Any] | None:
        u = self._fleet.get(uav_id)
        return u.snapshot() if u is not None else None

    def ingest_live_state(
        self,
        uav_id: str,
        *,
        route_id: str | None = None,
        waypoints: List[dict] | None = None,
        position: Dict[str, float] | None = None,
        waypoint_index: int | None = None,
        velocity_mps: float | None = None,
        battery_pct: float | None = None,
        flight_phase: str | None = None,
        armed: bool | None = None,
        active: bool | None = None,
        source: str = "live",
        source_meta: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        if route_id is not None:
            u.route_id = str(route_id)
        if isinstance(waypoints, list) and waypoints:
            parsed = normalize_waypoints(waypoints)
            if parsed:
                u.waypoints = parsed
        if isinstance(position, dict):
            pos = point_with_aliases(position)
            u.position = {
                "x": float(pos.get("x", u.position.get("x", 0.0))),
                "y": float(pos.get("y", u.position.get("y", 0.0))),
                "z": float(pos.get("z", u.position.get("z", 0.0))),
                "lon": float(pos.get("lon", u.position.get("lon", 0.0))),
                "lat": float(pos.get("lat", u.position.get("lat", 0.0))),
                "altM": float(pos.get("altM", u.position.get("altM", 0.0))),
            }
            # For remote/live UAV feeds, derive route progress from reported position
            # when explicit waypoint_index is not supplied.
            if waypoint_index is None and len(u.waypoints) >= 2:
                idx, t = self._project_position_to_route(u, u.position)
                u.waypoint_index = idx
                u.segment_progress = t
        elif u.waypoints:
            idx = max(0, min(int(waypoint_index or u.waypoint_index), len(u.waypoints) - 1))
            wp = u.waypoints[idx]
            u.position = point_with_aliases(wp)
        if waypoint_index is not None and u.waypoints:
            u.waypoint_index = max(0, min(int(waypoint_index), len(u.waypoints) - 1))
            u.segment_progress = 0.0
        if velocity_mps is not None:
            u.velocity_mps = float(velocity_mps)
        if battery_pct is not None:
            u.battery_pct = float(battery_pct)
        if flight_phase is not None:
            u.flight_phase = str(flight_phase)
        if armed is not None:
            u.armed = bool(armed)
        if active is not None:
            u.active = bool(active)
        u.data_source = str(source or "live")
        u.data_source_meta = dict(source_meta) if isinstance(source_meta, dict) else None
        self._refresh_route_progress(u)
        self._mark_update(u)
        return u.snapshot()

    def fleet_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {uav_id: u.snapshot() for uav_id, u in self._fleet.items()}

    def delete_uav(self, uav_id: str) -> bool:
        if uav_id in self._fleet:
            del self._fleet[uav_id]
            return True
        return False

    def load_fleet_snapshot(self, fleet: Dict[str, Dict[str, Any]] | None) -> None:
        if not isinstance(fleet, dict):
            return
        restored: Dict[str, SimUAV] = {}
        for uav_id, snap in fleet.items():
            if not isinstance(snap, dict):
                continue
            u = SimUAV(uav_id=str(snap.get("uav_id", uav_id)))
            u.route_id = str(snap.get("route_id", "route-1"))
            u.data_source = str(snap.get("data_source", "simulated") or "simulated")
            force_within_map = self._prefer_otaniemi_bounds(u.data_source)
            waypoints = snap.get("waypoints")
            if isinstance(waypoints, list) and waypoints:
                u.waypoints = self._sanitize_waypoints(waypoints, force_within_map=force_within_map)
            elif force_within_map:
                u.waypoints = normalize_waypoints(DEFAULT_ROUTE)
            u.waypoint_index = int(snap.get("waypoint_index", 0) or 0)
            if u.waypoints:
                u.waypoint_index = max(0, min(u.waypoint_index, len(u.waypoints) - 1))
            else:
                u.waypoint_index = 0
            u.segment_progress = max(0.0, min(1.0, float(snap.get("segment_progress", 0.0) or 0.0)))
            pos = snap.get("position")
            if isinstance(pos, dict):
                ref = u.waypoints[u.waypoint_index] if u.waypoints else point_with_aliases(DEFAULT_ROUTE[0])
                ref_lon, ref_lat, _ = extract_lon_lat_alt(ref)
                lon, lat, alt_m = extract_lon_lat_alt(pos)
                lon, lat = self._coerce_lon_lat(
                    lon,
                    lat,
                    ref_lon=ref_lon,
                    ref_lat=ref_lat,
                    force_within_map=force_within_map,
                )
                clean_pos = dict(pos)
                clean_pos["lon"] = float(lon)
                clean_pos["lat"] = float(lat)
                clean_pos["altM"] = max(0.0, float(alt_m))
                u.position = point_with_aliases(clean_pos)
            elif u.waypoints:
                u.position = point_with_aliases(u.waypoints[u.waypoint_index])
            u.velocity_mps = float(snap.get("velocity_mps", u.velocity_mps) or u.velocity_mps)
            u.battery_pct = float(snap.get("battery_pct", u.battery_pct) or u.battery_pct)
            u.distance_travelled_m = float(snap.get("distance_travelled_m", u.distance_travelled_m) or u.distance_travelled_m)
            u.route_progress_pct = float(snap.get("route_progress_pct", u.route_progress_pct) or u.route_progress_pct)
            u.flight_phase = str(snap.get("flight_phase", u.flight_phase))
            u.armed = bool(snap.get("armed", u.armed))
            u.active = bool(snap.get("active", u.active))
            u.last_update_ts = str(snap.get("last_update_ts", "") or "")
            if isinstance(snap.get("utm_approval"), dict):
                u.utm_approval = dict(snap["utm_approval"])  # type: ignore[index]
            if isinstance(snap.get("utm_geofence_result"), dict):
                u.utm_geofence_result = dict(snap["utm_geofence_result"])  # type: ignore[index]
            if isinstance(snap.get("data_source_meta"), dict):
                u.data_source_meta = dict(snap["data_source_meta"])  # type: ignore[index]
            self._refresh_route_progress(u)
            restored[u.uav_id] = u
        if restored:
            self._fleet = restored

    def _mark_update(self, u: SimUAV) -> None:
        u.last_update_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


SIM = UAVSimulator()
