from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from geo_utils import (
    extract_lon_lat_alt,
    haversine_m,
    horizontal_distance_m,
    is_valid_lon_lat,
    lon_lat_from_local_xy_m,
    normalize_no_fly_zones,
    point_with_aliases,
)
from uav_agent.simulator import SIM
from utm_agent.service import UTM_SERVICE


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _dist2d(a: Dict[str, float], b: Dict[str, float]) -> float:
    return horizontal_distance_m(a, b)


UAV_AGENT_BASE_URL = os.getenv("UAV_AGENT_BASE_URL", "http://127.0.0.1:8020").rstrip("/")
UTM_AGENT_BASE_URL = os.getenv("UTM_AGENT_BASE_URL", "http://127.0.0.1:8021").rstrip("/")
NETWORK_TRAFFIC_MODE = os.getenv("NETWORK_TRAFFIC_MODE", "sim").strip().lower() or "sim"
NETWORK_TELEMETRY_SOURCE_URL = os.getenv("NETWORK_TELEMETRY_SOURCE_URL", "").strip()
OTANIEMI_CENTER_LON = float(os.getenv("NETWORK_OTANIEMI_CENTER_LON", "24.8286") or 24.8286)
OTANIEMI_CENTER_LAT = float(os.getenv("NETWORK_OTANIEMI_CENTER_LAT", "60.1866") or 60.1866)
LEGACY_LOCAL_MAX_X_M = float(os.getenv("NETWORK_LEGACY_LOCAL_MAX_X_M", "2000") or 2000)
LEGACY_LOCAL_MAX_Y_M = float(os.getenv("NETWORK_LEGACY_LOCAL_MAX_Y_M", "2000") or 2000)
MAP_RELEVANCE_RADIUS_M = float(os.getenv("NETWORK_MAP_RELEVANCE_RADIUS_M", "20000") or 20000)


def _default_otaniemi_base_stations() -> List["BaseStation"]:
    return [
        BaseStation("BS-A", 24.82595, 60.18490, "n78", 3500, 100, 39, 30, 6, 58, "online"),
        BaseStation("BS-B", 24.83065, 60.18595, "n78", 3500, 80, 37, 27, 5, 66, "degraded"),
        BaseStation("BS-C", 24.83540, 60.18780, "n41", 2600, 60, 36, 24, 4, 52, "online"),
        BaseStation("BS-D", 24.83890, 60.18385, "n28", 700, 20, 43, 35, 7, 39, "online"),
        BaseStation("BS-E", 24.82360, 60.18870, "n78", 3500, 100, 40, 33, 6, 63, "online"),
    ]


def _http_get_json(url: str, timeout_s: float = 0.8) -> Dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            raw = resp.read()
        import json

        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


@dataclass
class BaseStation:
    id: str
    x: float
    y: float
    band: str
    freq_mhz: float
    bandwidth_mhz: float
    tx_power_dbm: float
    height_m: float
    tilt_deg: float
    load_pct: float
    status: str = "online"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "lon": self.x,
            "lat": self.y,
            "x": self.x,
            "y": self.y,
            "band": self.band,
            "freqMHz": self.freq_mhz,
            "bandwidthMHz": self.bandwidth_mhz,
            "txPowerDbm": round(self.tx_power_dbm, 1),
            "heightM": round(self.height_m, 1),
            "tiltDeg": round(self.tilt_deg, 1),
            "loadPct": round(self.load_pct, 1),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseStation":
        lon = float(data.get("lon", data.get("x", 0.0)))
        lat = float(data.get("lat", data.get("y", 0.0)))
        return cls(
            id=str(data.get("id", "BS")),
            x=lon,
            y=lat,
            band=str(data.get("band", "n78")),
            freq_mhz=float(data.get("freqMHz", data.get("freq_mhz", 3500.0))),
            bandwidth_mhz=float(data.get("bandwidthMHz", data.get("bandwidth_mhz", 100.0))),
            tx_power_dbm=float(data.get("txPowerDbm", data.get("tx_power_dbm", 35.0))),
            height_m=float(data.get("heightM", data.get("height_m", 30.0))),
            tilt_deg=float(data.get("tiltDeg", data.get("tilt_deg", 6.0))),
            load_pct=float(data.get("loadPct", data.get("load_pct", 50.0))),
            status=str(data.get("status", "online")),
        )


@dataclass
class TrackedUav:
    uav_id: str
    mission: str
    route: List[Dict[str, float]]
    route_index: int = 0
    t: float = 0.0
    speed_mps: float = 12.0
    qos_class: str = "telemetry"

    def current_position(self) -> Dict[str, float]:
        if not self.route:
            return point_with_aliases({"lon": 0.0, "lat": 0.0, "altM": 0.0})
        a = self.route[self.route_index % len(self.route)]
        b = self.route[(self.route_index + 1) % len(self.route)]
        return point_with_aliases(
            {
                "x": _lerp(float(a["x"]), float(b["x"]), self.t),
                "y": _lerp(float(a["y"]), float(b["y"]), self.t),
                "z": _lerp(float(a["z"]), float(b["z"]), self.t),
            }
        )

    def step(self, amount: float = 0.04) -> None:
        if len(self.route) < 2:
            return
        self.t += amount * _clamp(self.speed_mps / 15.0, 0.6, 1.4)
        while self.t >= 1.0:
            self.t -= 1.0
            self.route_index = (self.route_index + 1) % len(self.route)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.uav_id,
            "mission": self.mission,
            "route": [dict(p) for p in self.route],
            "routeIndex": self.route_index,
            "t": round(self.t, 4),
            "speedMps": round(self.speed_mps, 2),
            "qosClass": self.qos_class,
            "position": self.current_position(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrackedUav":
        route_raw = data.get("route")
        route = [dict(p) for p in route_raw] if isinstance(route_raw, list) else []
        return cls(
            uav_id=str(data.get("id", data.get("uav_id", "uav"))),
            mission=str(data.get("mission", "mission")),
            route=route,  # type: ignore[arg-type]
            route_index=int(data.get("routeIndex", data.get("route_index", 0)) or 0),
            t=float(data.get("t", 0.0) or 0.0),
            speed_mps=float(data.get("speedMps", data.get("speed_mps", 12.0)) or 12.0),
            qos_class=str(data.get("qosClass", data.get("qos_class", "telemetry"))),
        )


@dataclass
class NetworkMissionService:
    base_stations: List[BaseStation] = field(
        default_factory=_default_otaniemi_base_stations
    )
    tracked_uavs: Dict[str, TrackedUav] = field(default_factory=dict)
    last_tick_ts: str = ""
    latest_live_telemetry: Dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.base_stations = self._sanitize_base_stations(self.base_stations)

    def _station_anchor(self) -> tuple[float, float]:
        valid = [(float(bs.x), float(bs.y)) for bs in self.base_stations if is_valid_lon_lat(bs.x, bs.y)]
        if not valid:
            return OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT
        lon = sum(p[0] for p in valid) / len(valid)
        lat = sum(p[1] for p in valid) / len(valid)
        return lon, lat

    def _coerce_lon_lat(self, lon: float, lat: float, *, ref_lon: float | None = None, ref_lat: float | None = None) -> tuple[float, float]:
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
            return x, y
        if looks_local_xy:
            return lon_lat_from_local_xy_m(x, y, ref_lon=anchor_lon, ref_lat=anchor_lat)
        return anchor_lon, anchor_lat

    def _sanitize_base_stations(self, stations: List[BaseStation]) -> List[BaseStation]:
        if not stations:
            return _default_otaniemi_base_stations()
        out: List[BaseStation] = []
        for bs in stations:
            lon, lat = self._coerce_lon_lat(bs.x, bs.y, ref_lon=OTANIEMI_CENTER_LON, ref_lat=OTANIEMI_CENTER_LAT)
            if not is_valid_lon_lat(lon, lat):
                continue
            if haversine_m(lon, lat, OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT) > MAP_RELEVANCE_RADIUS_M:
                continue
            out.append(
                BaseStation(
                    id=str(bs.id),
                    x=float(lon),
                    y=float(lat),
                    band=str(bs.band),
                    freq_mhz=float(bs.freq_mhz),
                    bandwidth_mhz=float(bs.bandwidth_mhz),
                    tx_power_dbm=_clamp(float(bs.tx_power_dbm), 20.0, 50.0),
                    height_m=_clamp(float(bs.height_m), 5.0, 80.0),
                    tilt_deg=_clamp(float(bs.tilt_deg), 0.0, 15.0),
                    load_pct=_clamp(float(bs.load_pct), 0.0, 100.0),
                    status=str(bs.status if bs.status in {"online", "degraded", "maintenance"} else "online"),
                )
            )
        if len(out) < 3:
            return _default_otaniemi_base_stations()
        return out

    def _normalize_route_point(self, row: Dict[str, Any], *, ref_lon: float | None = None, ref_lat: float | None = None) -> Dict[str, float]:
        lon, lat, alt_m = extract_lon_lat_alt(row)
        lon, lat = self._coerce_lon_lat(lon, lat, ref_lon=ref_lon, ref_lat=ref_lat)
        return {"x": float(lon), "y": float(lat), "z": float(alt_m)}

    def _normalize_no_fly_zones_for_map(self, zones_raw: Any) -> List[Dict[str, Any]]:
        if not isinstance(zones_raw, list):
            return []
        anchor_lon, anchor_lat = self._station_anchor()
        out: List[Dict[str, Any]] = []
        for zone in normalize_no_fly_zones(zones_raw):
            lon = float(zone.get("lon", zone.get("cx", 0.0)))
            lat = float(zone.get("lat", zone.get("cy", 0.0)))
            lon, lat = self._coerce_lon_lat(lon, lat, ref_lon=anchor_lon, ref_lat=anchor_lat)
            if not is_valid_lon_lat(lon, lat):
                continue
            if haversine_m(lon, lat, anchor_lon, anchor_lat) > MAP_RELEVANCE_RADIUS_M:
                # Hide stale zones from other regions; network page should stay in active mission zone.
                continue
            z_min = float(zone.get("z_min", 0.0))
            z_max = float(zone.get("z_max", 120.0))
            if z_max < z_min:
                z_min, z_max = z_max, z_min
            item = dict(zone)
            item["lon"] = float(lon)
            item["lat"] = float(lat)
            item["cx"] = float(lon)
            item["cy"] = float(lat)
            item["radius_m"] = _clamp(float(zone.get("radius_m", 30.0)), 5.0, 5000.0)
            item["z_min"] = z_min
            item["z_max"] = z_max
            out.append(item)
        return out

    def export_state(self) -> Dict[str, Any]:
        return {
            "base_stations": [b.as_dict() for b in self.base_stations],
            "tracked_uavs": {uid: rec.as_dict() for uid, rec in self.tracked_uavs.items()},
            "last_tick_ts": self.last_tick_ts,
            "latest_live_telemetry": self.latest_live_telemetry,
        }

    def load_state(self, state: Dict[str, Any] | None) -> None:
        if not isinstance(state, dict):
            return
        bs_rows = state.get("base_stations")
        if isinstance(bs_rows, list):
            parsed = [BaseStation.from_dict(r) for r in bs_rows if isinstance(r, dict)]
            if parsed:
                self.base_stations = self._sanitize_base_stations(parsed)
        tracked_rows = state.get("tracked_uavs")
        if isinstance(tracked_rows, dict):
            parsed_uavs: Dict[str, TrackedUav] = {}
            ref_lon, ref_lat = self._station_anchor()
            for uid, row in tracked_rows.items():
                if not isinstance(row, dict):
                    continue
                rec = TrackedUav.from_dict(row)
                rec.route = [self._normalize_route_point(p, ref_lon=ref_lon, ref_lat=ref_lat) for p in rec.route if isinstance(p, dict)]
                if len(rec.route) < 2:
                    continue
                rec.route_index = rec.route_index % len(rec.route)
                parsed_uavs[str(uid)] = rec
            if parsed_uavs:
                self.tracked_uavs = parsed_uavs
        if isinstance(state.get("last_tick_ts"), str):
            self.last_tick_ts = str(state["last_tick_ts"])
        if isinstance(state.get("latest_live_telemetry"), dict):
            normalized_live = self._normalize_live_telemetry(dict(state["latest_live_telemetry"]))
            self.latest_live_telemetry = normalized_live if isinstance(normalized_live, dict) else None

    def traffic_mode(self) -> str:
        mode = NETWORK_TRAFFIC_MODE
        return mode if mode in {"sim", "real", "auto"} else "sim"

    def traffic_source_config(self) -> Dict[str, Any]:
        return {
            "mode": self.traffic_mode(),
            "pullUrl": NETWORK_TELEMETRY_SOURCE_URL or None,
            "hasPushedTelemetry": isinstance(self.latest_live_telemetry, dict),
        }

    def ingest_live_telemetry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        normalized = self._normalize_live_telemetry(payload)
        if not normalized:
            return {"status": "error", "error": "invalid_live_telemetry_payload"}
        normalized["receivedAt"] = now
        self.latest_live_telemetry = normalized
        return {"status": "success", "result": {"receivedAt": now, "source": normalized.get("source", "push"), "trafficMode": self.traffic_mode()}}

    def _coverage_radius(self, bs: BaseStation) -> float:
        low_boost = 260 if bs.freq_mhz < 1000 else 130 if bs.freq_mhz < 3000 else 70
        return _clamp(140 + (bs.tx_power_dbm - 34) * 16 + bs.bandwidth_mhz * 0.45 + low_boost - bs.load_pct * 0.9, 120, 650)

    def _signal_at(self, bs: BaseStation, pos: Dict[str, float]) -> float:
        d2d = max(2.0, _dist2d({"lon": bs.x, "lat": bs.y}, pos))
        d3d = math.sqrt(d2d * d2d + max(1.0, bs.height_m - float(pos["z"])) ** 2)
        path_loss = 32.4 + 20 * math.log10(d3d / 100.0) + 20 * math.log10(max(700.0, bs.freq_mhz) / 1000.0)
        load_penalty = bs.load_pct * 0.07
        tilt_penalty = abs(bs.tilt_deg - 6.0) * 0.4
        return bs.tx_power_dbm - path_loss - load_penalty - tilt_penalty + 28.0

    def _fetch_remote_uav_fleet(self) -> Dict[str, Dict[str, Any]] | None:
        data = _http_get_json(f"{UAV_AGENT_BASE_URL}/api/uav/sim/fleet")
        if not data or data.get("status") != "success":
            return None
        result = data.get("result")
        if not isinstance(result, dict):
            return None
        fleet = result.get("fleet")
        return fleet if isinstance(fleet, dict) else None

    def _fetch_remote_utm_state(self, airspace_segment: str) -> Dict[str, Any] | None:
        q = urllib.parse.urlencode({"airspace_segment": airspace_segment})
        data = _http_get_json(f"{UTM_AGENT_BASE_URL}/api/utm/state?{q}")
        if not data or data.get("status") != "success":
            return None
        result = data.get("result")
        return result if isinstance(result, dict) else None

    def _sync_tracked_uavs_from_sim(self) -> None:
        fleet = self._fetch_remote_uav_fleet() or SIM.fleet_snapshot()
        next_ids: set[str] = set()
        ref_lon, ref_lat = self._station_anchor()
        for uav_id, snap in fleet.items():
            if not isinstance(snap, dict):
                continue
            waypoints = snap.get("waypoints")
            if not isinstance(waypoints, list) or len(waypoints) < 2:
                continue
            route: List[Dict[str, float]] = []
            for wp in waypoints:
                if not isinstance(wp, dict):
                    continue
                lon, lat, alt_m = extract_lon_lat_alt(wp)
                lon, lat = self._coerce_lon_lat(lon, lat, ref_lon=ref_lon, ref_lat=ref_lat)
                route.append(
                    {
                        "x": float(lon),
                        "y": float(lat),
                        "z": float(alt_m),
                    }
                )
            if len(route) < 2:
                continue
            next_ids.add(str(uav_id))
            rec = self.tracked_uavs.get(uav_id)
            if rec is None:
                rec = TrackedUav(uav_id=uav_id, mission="sim mission", route=route, qos_class="control", speed_mps=float(snap.get("velocity_mps", 12.0) or 12.0))
                self.tracked_uavs[uav_id] = rec
            rec.route = route
            rec.speed_mps = float(snap.get("velocity_mps", rec.speed_mps) or rec.speed_mps)
            if isinstance(snap.get("position"), dict):
                pos = snap["position"]
                try:
                    plon, plat, palt = extract_lon_lat_alt(pos)
                    plon, plat = self._coerce_lon_lat(plon, plat, ref_lon=ref_lon, ref_lat=ref_lat)
                    px = float(plon if isinstance(plon, (int, float)) else route[0]["x"])  # type: ignore[union-attr]
                    py = float(plat if isinstance(plat, (int, float)) else route[0]["y"])  # type: ignore[union-attr]
                    pz = float(palt if isinstance(palt, (int, float)) else route[0]["z"])  # type: ignore[union-attr]
                    # Use the simulator's reported position directly by replacing interpolation with a single-point progress near current waypoint.
                    rec.route_index = int(snap.get("waypoint_index", rec.route_index) or 0) % len(route)
                    rec.t = 0.0
                    rec.route[rec.route_index] = {"x": px, "y": py, "z": pz}
                except Exception:
                    pass
        # Keep only UAVs that currently exist in the simulator fleet to avoid stale synthetic tracks.
        if isinstance(fleet, dict):
            self.tracked_uavs = {uid: rec for uid, rec in self.tracked_uavs.items() if uid in next_ids}

    def _fetch_external_live_telemetry(self) -> Dict[str, Any] | None:
        if not NETWORK_TELEMETRY_SOURCE_URL:
            return None
        data = _http_get_json(NETWORK_TELEMETRY_SOURCE_URL, timeout_s=1.2)
        if not isinstance(data, dict):
            return None
        candidate = data.get("result") if isinstance(data.get("result"), dict) else data
        return self._normalize_live_telemetry(candidate if isinstance(candidate, dict) else {})

    def _normalize_live_telemetry(self, payload: Dict[str, Any]) -> Dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        snapshots_raw = payload.get("trackingSnapshots")
        kpis_raw = payload.get("networkKpis")
        if not isinstance(snapshots_raw, list) or not isinstance(kpis_raw, dict):
            # Accept flatter payloads from custom exporters.
            snapshots_raw = payload.get("snapshots", snapshots_raw)
            kpis_raw = payload.get("kpis", kpis_raw)
        if not isinstance(snapshots_raw, list) or not isinstance(kpis_raw, dict):
            return None
        snapshots: List[Dict[str, Any]] = []
        ref_lon, ref_lat = self._station_anchor()
        for row in snapshots_raw:
            if not isinstance(row, dict):
                continue
            try:
                lon_raw = float(row.get("lon", row.get("x", 0.0)))
                lat_raw = float(row.get("lat", row.get("y", 0.0)))
                lon, lat = self._coerce_lon_lat(lon_raw, lat_raw, ref_lon=ref_lon, ref_lat=ref_lat)
                snapshots.append(
                    {
                        "id": str(row.get("id", row.get("uavId", "uav"))),
                        "mission": str(row.get("mission", row.get("missionId", "live"))),
                        "qosClass": str(row.get("qosClass", row.get("qos_class", "telemetry"))),
                        "lon": float(lon),
                        "lat": float(lat),
                        "altM": float(row.get("altM", row.get("z", 0.0))),
                        "x": float(lon),
                        "y": float(lat),
                        "z": float(row.get("altM", row.get("z", 0.0))),
                        "headingDeg": float(row.get("headingDeg", row.get("heading_deg", 0.0))),
                        "speedMps": float(row.get("speedMps", row.get("speed_mps", 0.0))),
                        "attachedBsId": str(row.get("attachedBsId", row.get("cellId", row.get("servingCellId", "N/A")))),
                        "rsrpDbm": float(row.get("rsrpDbm", row.get("rsrp_dbm", -120.0))),
                        "sinrDb": float(row.get("sinrDb", row.get("sinr_db", 0.0))),
                        "latencyMs": float(row.get("latencyMs", row.get("latency_ms", 0.0))),
                        "packetLossPct": float(row.get("packetLossPct", row.get("packet_loss_pct", 0.0))),
                        "trackingConfidencePct": float(row.get("trackingConfidencePct", row.get("tracking_confidence_pct", 100.0))),
                        "interferenceRisk": str(row.get("interferenceRisk", row.get("risk", "unknown"))),
                    }
                )
            except Exception:
                continue
        if not snapshots:
            return None
        try:
            kpis = {
                "coverageScorePct": float(kpis_raw.get("coverageScorePct", kpis_raw.get("coverage_score_pct", 0.0))),
                "avgSinrDb": float(kpis_raw.get("avgSinrDb", kpis_raw.get("avg_sinr_db", 0.0))),
                "avgLatencyMs": float(kpis_raw.get("avgLatencyMs", kpis_raw.get("avg_latency_ms", 0.0))),
                "highInterferenceRiskCount": int(kpis_raw.get("highInterferenceRiskCount", kpis_raw.get("high_interference_risk_count", 0))),
                "utmTrackingHealthPct": float(kpis_raw.get("utmTrackingHealthPct", kpis_raw.get("utm_tracking_health_pct", 0.0))),
            }
        except Exception:
            return None
        normalized: Dict[str, Any] = {
            "timestamp": str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            "trackingSnapshots": snapshots,
            "networkKpis": kpis,
            "selectedTracking": payload.get("selectedTracking"),
            "source": str(payload.get("source") or ("pull" if NETWORK_TELEMETRY_SOURCE_URL else "push")),
        }
        if isinstance(payload.get("baseStations"), list):
            live_bs: List[Dict[str, Any]] = []
            for row in payload.get("baseStations"):
                if not isinstance(row, dict):
                    continue
                try:
                    lon, lat = self._coerce_lon_lat(
                        float(row.get("lon", row.get("x", 0.0))),
                        float(row.get("lat", row.get("y", 0.0))),
                        ref_lon=ref_lon,
                        ref_lat=ref_lat,
                    )
                    if not is_valid_lon_lat(lon, lat):
                        continue
                    live_bs.append(
                        {
                            "id": str(row.get("id", "BS")),
                            "lon": float(lon),
                            "lat": float(lat),
                            "x": float(lon),
                            "y": float(lat),
                            "band": str(row.get("band", "n78")),
                            "freqMHz": float(row.get("freqMHz", row.get("freq_mhz", 3500.0))),
                            "bandwidthMHz": float(row.get("bandwidthMHz", row.get("bandwidth_mhz", 100.0))),
                            "txPowerDbm": float(row.get("txPowerDbm", row.get("tx_power_dbm", 35.0))),
                            "heightM": float(row.get("heightM", row.get("height_m", 30.0))),
                            "tiltDeg": float(row.get("tiltDeg", row.get("tilt_deg", 6.0))),
                            "loadPct": float(row.get("loadPct", row.get("load_pct", 50.0))),
                            "status": str(row.get("status", "online")),
                        }
                    )
                except Exception:
                    continue
            if live_bs:
                normalized["baseStations"] = live_bs
        if isinstance(payload.get("coverage"), list):
            live_cov: List[Dict[str, Any]] = []
            for row in payload.get("coverage"):
                if not isinstance(row, dict):
                    continue
                try:
                    live_cov.append(
                        {
                            "bsId": str(row.get("bsId", "")),
                            "radiusM": round(_clamp(float(row.get("radiusM", row.get("radius_m", 0.0))), 40.0, 5000.0), 1),
                        }
                    )
                except Exception:
                    continue
            if live_cov:
                normalized["coverage"] = live_cov
        return normalized

    def tick(self, steps: int = 1) -> Dict[str, Any]:
        self._sync_tracked_uavs_from_sim()
        # In "real testing" mode the UAV backend is the source of truth for motion.
        # Network ticks refresh tracking/metrics timestamps only and should not move UAVs independently.
        self.last_tick_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {"status": "success", "steps": max(1, int(steps)), "last_tick_ts": self.last_tick_ts}

    def update_base_station(self, bs_id: str, **fields: Any) -> Dict[str, Any]:
        for bs in self.base_stations:
            if bs.id != bs_id:
                continue
            if "txPowerDbm" in fields:
                bs.tx_power_dbm = _clamp(float(fields["txPowerDbm"]), 20.0, 50.0)
            if "tiltDeg" in fields:
                bs.tilt_deg = _clamp(float(fields["tiltDeg"]), 0.0, 15.0)
            if "loadPct" in fields:
                bs.load_pct = _clamp(float(fields["loadPct"]), 0.0, 100.0)
            if "status" in fields and fields["status"] in {"online", "degraded", "maintenance"}:
                bs.status = str(fields["status"])
            return {"status": "success", "result": bs.as_dict()}
        return {"status": "error", "error": "base_station_not_found", "bs_id": bs_id}

    def reset_runtime(self, *, clear_live_telemetry: bool = True, keep_base_stations: bool = True) -> Dict[str, Any]:
        tracked_before = len(self.tracked_uavs)
        had_live = isinstance(self.latest_live_telemetry, dict)
        self.tracked_uavs = {}
        if clear_live_telemetry:
            self.latest_live_telemetry = None
        if not keep_base_stations:
            self.base_stations = self._sanitize_base_stations(_default_otaniemi_base_stations())
        self.last_tick_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "status": "success",
            "result": {
                "trackedCleared": tracked_before,
                "liveTelemetryCleared": bool(clear_live_telemetry and had_live),
                "baseStationsReset": bool(not keep_base_stations),
                "baseStationCount": len(self.base_stations),
                "lastTickTs": self.last_tick_ts,
            },
        }

    def apply_optimization(self, mode: str, coverage_target_pct: float = 96.0, max_tx_cap_dbm: float = 41.0, qos_priority_weight: float = 68.0) -> Dict[str, Any]:
        max_cap = _clamp(float(max_tx_cap_dbm), 30.0, 46.0)
        cov_target = _clamp(float(coverage_target_pct), 70.0, 100.0)
        qos_w = _clamp(float(qos_priority_weight), 0.0, 100.0)
        for bs in self.base_stations:
            tx = bs.tx_power_dbm
            load = bs.load_pct
            tilt = bs.tilt_deg
            if mode == "coverage":
                tx = _clamp(tx + (1.2 if cov_target >= 95 else 0.4), 30.0, max_cap)
                tilt = _clamp(tilt - 0.6, 2.0, 10.0)
            elif mode == "power":
                tx = _clamp(tx - 1.5, 30.0, max_cap)
                load = _clamp(load - 2.0, 0.0, 100.0)
            elif mode == "qos":
                tx = _clamp(tx + (qos_w / 100.0) * 0.9, 30.0, max_cap)
                load = _clamp(load - (qos_w / 100.0) * 1.8, 0.0, 100.0)
                tilt = _clamp(tilt + 0.4, 2.0, 10.0)
            else:
                return {"status": "error", "error": "invalid_mode", "mode": mode}
            bs.tx_power_dbm = round(tx, 1)
            bs.load_pct = round(load, 1)
            bs.tilt_deg = round(tilt, 1)
        return {"status": "success", "result": {"mode": mode, "coverageTargetPct": cov_target, "maxTxCapDbm": max_cap, "qosPriorityWeight": qos_w}}

    def _snapshot_for_uav(self, uav: TrackedUav) -> Dict[str, Any]:
        pos = uav.current_position()
        scored = [{"bs": bs, "rsrp": self._signal_at(bs, pos)} for bs in self.base_stations]
        scored.sort(key=lambda r: r["rsrp"], reverse=True)
        serving = scored[0] if scored else None
        interferer = scored[1] if len(scored) > 1 else None
        rsrp = float(serving["rsrp"]) if serving else -120.0
        interferer_rsrp = float(interferer["rsrp"]) if interferer else -130.0
        sinr = _clamp(rsrp - (interferer_rsrp + 3.0), -8.0, 35.0)
        a = uav.route[uav.route_index % len(uav.route)] if uav.route else pos
        b = uav.route[(uav.route_index + 1) % len(uav.route)] if len(uav.route) > 1 else pos
        heading = (math.degrees(math.atan2(float(b["y"]) - float(a["y"]), float(b["x"]) - float(a["x"]))) + 360.0) % 360.0
        latency = _clamp(14 + (12 if uav.qos_class == "video" else 4) + ((serving["bs"].load_pct if serving else 50.0) * 0.24) - sinr * 0.2, 8, 85)
        packet_loss = _clamp((0.8 if uav.qos_class == "video" else 0.25) + ((6 - sinr) * 0.22 if sinr < 6 else 0.0) + (0.5 if interferer_rsrp > -82 else 0.0), 0.05, 9)
        confidence = _clamp(98 - packet_loss * 4.2 - max(0.0, 20.0 - sinr) * 0.7, 62.0, 99.5)
        risk = "high" if sinr < 6 else "medium" if sinr < 14 else "low"
        return {
            "id": uav.uav_id,
            "mission": uav.mission,
            "qosClass": uav.qos_class,
            "lon": round(float(pos["x"]), 7),
            "lat": round(float(pos["y"]), 7),
            "altM": round(float(pos["z"]), 2),
            "x": round(float(pos["x"]), 2),
            "y": round(float(pos["y"]), 2),
            "z": round(float(pos["z"]), 2),
            "headingDeg": round(heading, 1),
            "speedMps": round(uav.speed_mps, 2),
            "attachedBsId": serving["bs"].id if serving else "N/A",
            "rsrpDbm": round(rsrp, 1),
            "sinrDb": round(float(sinr), 1),
            "latencyMs": round(float(latency), 1),
            "packetLossPct": round(float(packet_loss), 2),
            "trackingConfidencePct": round(float(confidence), 1),
            "interferenceRisk": risk,
        }

    def get_state(self, airspace_segment: str = "sector-A3", selected_uav_id: str | None = None) -> Dict[str, Any]:
        self._sync_tracked_uavs_from_sim()
        mode = self.traffic_mode()
        live_telemetry = self._fetch_external_live_telemetry() or (dict(self.latest_live_telemetry) if isinstance(self.latest_live_telemetry, dict) else None)
        tracked = list(self.tracked_uavs.values())
        anchor_lon, anchor_lat = self._station_anchor()
        using_live = bool(live_telemetry and mode in {"real", "auto"})
        if mode == "real" and not live_telemetry:
            return {
                "status": "error",
                "error": "real_traffic_unavailable",
                "detail": "Configure NETWORK_TELEMETRY_SOURCE_URL or push telemetry to /api/network/telemetry/ingest",
                "result": {"trafficSource": {"mode": mode, "active": "none", "config": self.traffic_source_config()}},
            }
        if using_live and isinstance(live_telemetry, dict):
            snapshots = [dict(s) for s in live_telemetry.get("trackingSnapshots", []) if isinstance(s, dict)]
            filtered_snapshots = [
                s
                for s in snapshots
                if (
                    is_valid_lon_lat(float(s.get("lon", s.get("x", 0.0))), float(s.get("lat", s.get("y", 0.0))))
                    and haversine_m(
                        float(s.get("lon", s.get("x", 0.0))),
                        float(s.get("lat", s.get("y", 0.0))),
                        anchor_lon,
                        anchor_lat,
                    )
                    <= MAP_RELEVANCE_RADIUS_M
                )
            ]
            if filtered_snapshots:
                snapshots = filtered_snapshots
            selected = next((s for s in snapshots if s.get("id") == selected_uav_id), None)
            if selected is None:
                selected_raw = next((s for s in [live_telemetry.get("selectedTracking")] if isinstance(s, dict)), None)
                if selected_raw:
                    lon, lat = self._coerce_lon_lat(
                        float(selected_raw.get("lon", selected_raw.get("x", 0.0))),
                        float(selected_raw.get("lat", selected_raw.get("y", 0.0))),
                        ref_lon=anchor_lon,
                        ref_lat=anchor_lat,
                    )
                    selected = dict(selected_raw)
                    selected["lon"] = float(lon)
                    selected["lat"] = float(lat)
                    selected["x"] = float(lon)
                    selected["y"] = float(lat)
            if selected is None and snapshots:
                selected = snapshots[0]
            kpis_live = live_telemetry.get("networkKpis")
            kpis = dict(kpis_live) if isinstance(kpis_live, dict) else {}
        else:
            snapshots = [self._snapshot_for_uav(u) for u in tracked]
            avg_sinr = sum(s["sinrDb"] for s in snapshots) / len(snapshots) if snapshots else 0.0
            avg_latency = sum(s["latencyMs"] for s in snapshots) / len(snapshots) if snapshots else 0.0
            high_risk = sum(1 for s in snapshots if s["interferenceRisk"] == "high")
            coverage_score = _clamp(
                93
                + sum(1 for b in self.base_stations if self._coverage_radius(b) > 220) * 1.1
                - sum(1 for b in self.base_stations if b.status != "online") * 2.4
                - high_risk * 1.8,
                72,
                99,
            )
            utm_health = _clamp(99 - high_risk * 6 - max(0.0, 12.0 - avg_sinr) * 1.2, 70, 99.4)
            selected = next((s for s in snapshots if s["id"] == selected_uav_id), None)
            if selected is None and snapshots:
                selected = snapshots[0]
            kpis = {
                "coverageScorePct": round(float(coverage_score), 1),
                "avgSinrDb": round(float(avg_sinr), 1),
                "avgLatencyMs": round(float(avg_latency), 1),
                "highInterferenceRiskCount": high_risk,
                "utmTrackingHealthPct": round(float(utm_health), 1),
            }
        remote_utm = self._fetch_remote_utm_state(airspace_segment)
        utm_payload = remote_utm or {
            "weather": UTM_SERVICE.get_weather(airspace_segment),
            "weatherChecks": UTM_SERVICE.check_weather(airspace_segment),
            "noFlyZones": list(UTM_SERVICE.no_fly_zones),
            "regulations": dict(UTM_SERVICE.regulations),
            "regulationProfiles": {k: dict(v) for k, v in UTM_SERVICE.regulation_profiles.items()},
            "effectiveRegulations": UTM_SERVICE.effective_regulations(None),
            "licenses": dict(UTM_SERVICE.operator_licenses),
        }
        utm_payload = dict(utm_payload) if isinstance(utm_payload, dict) else {}
        no_fly_raw = utm_payload.get("noFlyZones")
        if not isinstance(no_fly_raw, list) and isinstance(utm_payload.get("no_fly_zones"), list):
            no_fly_raw = utm_payload.get("no_fly_zones")
        utm_payload["noFlyZones"] = self._normalize_no_fly_zones_for_map(no_fly_raw)
        return {
            "status": "success",
            "result": {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "lastTickTs": self.last_tick_ts,
                "airspaceSegment": airspace_segment,
                "trafficSource": {
                    "mode": mode,
                    "active": "live" if using_live else "simulated",
                    "config": self.traffic_source_config(),
                    "liveTimestamp": (live_telemetry.get("timestamp") if isinstance(live_telemetry, dict) else None),
                    "liveReceivedAt": (live_telemetry.get("receivedAt") if isinstance(live_telemetry, dict) else None),
                },
                "baseStations": (
                    live_telemetry.get("baseStations")
                    if using_live and isinstance(live_telemetry, dict) and isinstance(live_telemetry.get("baseStations"), list)
                    else [b.as_dict() for b in self.base_stations]
                ),
                "coverage": (
                    live_telemetry.get("coverage")
                    if using_live and isinstance(live_telemetry, dict) and isinstance(live_telemetry.get("coverage"), list)
                    else [{"bsId": b.id, "radiusM": round(self._coverage_radius(b), 1)} for b in self.base_stations]
                ),
                "uavs": [u.as_dict() for u in tracked],
                "trackingSnapshots": snapshots,
                "selectedTracking": selected,
                "networkKpis": kpis,
                "utm": utm_payload,
                "simFleet": self._fetch_remote_uav_fleet() or SIM.fleet_snapshot(),
            },
        }


NETWORK_MISSION_SERVICE = NetworkMissionService()
