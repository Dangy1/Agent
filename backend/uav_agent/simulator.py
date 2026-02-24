from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


DEFAULT_ROUTE = [
    {"x": 0.0, "y": 0.0, "z": 0.0},
    {"x": 100.0, "y": 50.0, "z": 40.0},
    {"x": 200.0, "y": 120.0, "z": 55.0},
    {"x": 260.0, "y": 180.0, "z": 45.0},
]


@dataclass
class SimUAV:
    uav_id: str
    route_id: str = "route-1"
    waypoints: List[dict] = field(default_factory=lambda: [dict(w) for w in DEFAULT_ROUTE])
    waypoint_index: int = 0
    position: Dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    velocity_mps: float = 12.0
    battery_pct: float = 100.0
    flight_phase: str = "IDLE"
    armed: bool = False
    active: bool = False
    last_update_ts: str = ""
    utm_approval: Dict[str, Any] | None = None
    utm_geofence_result: Dict[str, Any] | None = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "uav_id": self.uav_id,
            "route_id": self.route_id,
            "waypoint_index": self.waypoint_index,
            "waypoints_total": len(self.waypoints),
            "waypoints": [dict(w) for w in self.waypoints],
            "position": dict(self.position),
            "velocity_mps": self.velocity_mps,
            "battery_pct": round(self.battery_pct, 2),
            "flight_phase": self.flight_phase,
            "armed": self.armed,
            "active": self.active,
            "last_update_ts": self.last_update_ts,
            "utm_approval": self.utm_approval,
            "utm_geofence_result": self.utm_geofence_result,
        }


class UAVSimulator:
    def __init__(self) -> None:
        self._fleet: Dict[str, SimUAV] = {}

    def get_or_create(self, uav_id: str) -> SimUAV:
        if uav_id not in self._fleet:
            self._fleet[uav_id] = SimUAV(uav_id=uav_id)
        return self._fleet[uav_id]

    def plan_route(self, uav_id: str, route_id: str, waypoints: List[dict] | None = None) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.route_id = route_id
        u.waypoints = [dict(w) for w in (DEFAULT_ROUTE if waypoints is None else waypoints)]
        u.waypoint_index = 0
        first = u.waypoints[0] if u.waypoints else {"x": 0.0, "y": 0.0, "z": 0.0}
        u.position = {"x": float(first.get("x", 0.0)), "y": float(first.get("y", 0.0)), "z": float(first.get("z", 0.0))}
        u.flight_phase = "PLANNED"
        u.active = False
        u.armed = False
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
        u.armed = True
        u.active = True
        u.flight_phase = "TAKEOFF" if u.waypoint_index == 0 else "MISSION"
        self._mark_update(u)
        return u.snapshot()

    def step(self, uav_id: str, ticks: int = 1) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        ticks = max(1, int(ticks))
        for _ in range(ticks):
            if not u.active:
                break
            if u.waypoint_index >= len(u.waypoints) - 1:
                u.flight_phase = "LOITER"
                u.active = False
                break
            u.waypoint_index += 1
            wp = u.waypoints[u.waypoint_index]
            u.position = {"x": float(wp.get("x", 0.0)), "y": float(wp.get("y", 0.0)), "z": float(wp.get("z", 0.0))}
            u.flight_phase = "MISSION" if u.waypoint_index < len(u.waypoints) - 1 else "ARRIVAL"
            u.battery_pct = max(0.0, u.battery_pct - 0.8)
            if u.battery_pct < 15.0:
                u.flight_phase = "LOW_BATTERY"
        self._mark_update(u)
        return u.snapshot()

    def hold(self, uav_id: str, reason: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.active = False
        u.flight_phase = "HOLD"
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
        self._mark_update(u)
        return u.snapshot()

    def rth(self, uav_id: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.active = False
        u.flight_phase = "RTH"
        if u.waypoints:
            home = u.waypoints[0]
            u.position = {"x": float(home.get("x", 0.0)), "y": float(home.get("y", 0.0)), "z": float(home.get("z", 0.0))}
        self._mark_update(u)
        return u.snapshot()

    def land(self, uav_id: str) -> Dict[str, Any]:
        u = self.get_or_create(uav_id)
        u.active = False
        u.armed = False
        u.flight_phase = "LAND"
        u.position["z"] = 0.0
        self._mark_update(u)
        return u.snapshot()

    def status(self, uav_id: str) -> Dict[str, Any]:
        return self.get_or_create(uav_id).snapshot()

    def fleet_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {uav_id: u.snapshot() for uav_id, u in self._fleet.items()}

    def load_fleet_snapshot(self, fleet: Dict[str, Dict[str, Any]] | None) -> None:
        if not isinstance(fleet, dict):
            return
        restored: Dict[str, SimUAV] = {}
        for uav_id, snap in fleet.items():
            if not isinstance(snap, dict):
                continue
            u = SimUAV(uav_id=str(snap.get("uav_id", uav_id)))
            u.route_id = str(snap.get("route_id", "route-1"))
            waypoints = snap.get("waypoints")
            if isinstance(waypoints, list) and waypoints:
                u.waypoints = [dict(w) for w in waypoints if isinstance(w, dict)]
            u.waypoint_index = int(snap.get("waypoint_index", 0) or 0)
            pos = snap.get("position")
            if isinstance(pos, dict):
                u.position = {
                    "x": float(pos.get("x", 0.0)),
                    "y": float(pos.get("y", 0.0)),
                    "z": float(pos.get("z", 0.0)),
                }
            u.velocity_mps = float(snap.get("velocity_mps", u.velocity_mps) or u.velocity_mps)
            u.battery_pct = float(snap.get("battery_pct", u.battery_pct) or u.battery_pct)
            u.flight_phase = str(snap.get("flight_phase", u.flight_phase))
            u.armed = bool(snap.get("armed", u.armed))
            u.active = bool(snap.get("active", u.active))
            u.last_update_ts = str(snap.get("last_update_ts", "") or "")
            if isinstance(snap.get("utm_approval"), dict):
                u.utm_approval = dict(snap["utm_approval"])  # type: ignore[index]
            if isinstance(snap.get("utm_geofence_result"), dict):
                u.utm_geofence_result = dict(snap["utm_geofence_result"])  # type: ignore[index]
            restored[u.uav_id] = u
        if restored:
            self._fleet = restored

    def _mark_update(self, u: SimUAV) -> None:
        u.last_update_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


SIM = UAVSimulator()
