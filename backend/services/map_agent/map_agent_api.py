from __future__ import annotations

from datetime import datetime, timezone
import re
import threading
from typing import Any, Dict

from agent_db import AgentDB

from .config import load_map_agent_config
from .core.data_fetcher import MAP_DATA_FETCHER, MapDataFetcher
from .core.translator import gps_to_geojson_feature
from .engines import DEFAULT_ENGINE_ID, supported_engine_ids, supported_engines


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list_of_dicts(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, dict)]


def _normalize_scope(raw: Any) -> str:
    value = str(raw or "shared").strip().lower()
    if not value:
        return "shared"
    clean = re.sub(r"[^a-z0-9_\-:.]", "-", value)
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    if not clean:
        return "shared"
    return clean[:80]


class MapSyncBroker:
    _STATE_SCOPES_KEY = "sync_scopes_v1"
    _STATE_EVENTS_KEY = "sync_events_v1"
    _STATE_EVENT_SEQ_KEY = "sync_event_seq_v1"

    def __init__(self, *, db: AgentDB | None = None, max_events: int = 2000) -> None:
        self._db = db or AgentDB("map")
        self._lock = threading.Lock()
        self._max_events = max(200, int(_to_float(max_events, 2000.0)))

    @staticmethod
    def _empty_scope_state() -> Dict[str, Any]:
        return {
            "updatedAt": _to_iso_now(),
            "layers": {
                "uavs": {},
                "paths": {},
                "noFlyZones": {},
                "baseStations": {},
                "coverage": {},
                "points": {},
            },
        }

    def _load_scopes_locked(self) -> Dict[str, Any]:
        raw = self._db.get_state(self._STATE_SCOPES_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_scopes_locked(self, scopes: Dict[str, Any]) -> None:
        self._db.set_state(self._STATE_SCOPES_KEY, scopes)

    def _ensure_scope_locked(self, scopes: Dict[str, Any], scope: str) -> Dict[str, Any]:
        scope_state = scopes.get(scope)
        if not isinstance(scope_state, dict):
            scope_state = self._empty_scope_state()
            scopes[scope] = scope_state
        layers = scope_state.get("layers")
        if not isinstance(layers, dict):
            layers = {}
            scope_state["layers"] = layers
        for layer in ("uavs", "paths", "noFlyZones", "baseStations", "coverage", "points"):
            if not isinstance(layers.get(layer), dict):
                layers[layer] = {}
        return scope_state

    def _record_event_locked(self, *, scope: str, source: str, topic: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prev_seq = int(_to_float(self._db.get_state(self._STATE_EVENT_SEQ_KEY), 0.0))
        next_seq = max(0, prev_seq) + 1
        event = {
            "id": next_seq,
            "scope": scope,
            "source": str(source or "unknown"),
            "topic": str(topic or "update"),
            "payload": payload,
            "createdAt": _to_iso_now(),
        }
        events_raw = self._db.get_state(self._STATE_EVENTS_KEY)
        events = [dict(row) for row in events_raw if isinstance(row, dict)] if isinstance(events_raw, list) else []
        events.append(event)
        if len(events) > self._max_events:
            events = events[-self._max_events :]
        self._db.set_state(self._STATE_EVENT_SEQ_KEY, next_seq)
        self._db.set_state(self._STATE_EVENTS_KEY, events)
        sync = self._db.record_action(
            "sync_publish",
            payload={"scope": scope, "source": source, "topic": topic},
            result={"event_id": next_seq},
            entity_id=scope,
        )
        return {"event": event, "sync": sync}

    @staticmethod
    def _rows_to_layer_map(rows: list[Dict[str, Any]], *, key_candidates: tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for idx, row in enumerate(rows):
            key = ""
            for candidate in key_candidates:
                value = row.get(candidate)
                if isinstance(value, str) and value.strip():
                    key = value.strip()
                    break
            if not key:
                key = f"item-{idx + 1}"
            out[key] = dict(row)
        return out

    @staticmethod
    def _layer_rows(scope_state: Dict[str, Any], layer: str) -> Dict[str, Dict[str, Any]]:
        layers = scope_state.get("layers")
        if not isinstance(layers, dict):
            return {}
        rows = layers.get(layer)
        return dict(rows) if isinstance(rows, dict) else {}

    @staticmethod
    def _set_layer(scope_state: Dict[str, Any], layer: str, row_map: Dict[str, Dict[str, Any]]) -> None:
        layers = scope_state.get("layers")
        if not isinstance(layers, dict):
            layers = {}
            scope_state["layers"] = layers
        layers[layer] = row_map
        scope_state["updatedAt"] = _to_iso_now()

    @staticmethod
    def _normalize_xy(row: Dict[str, Any]) -> Dict[str, Any]:
        lon = _to_float(row.get("lon", row.get("x", 0.0)), 0.0)
        lat = _to_float(row.get("lat", row.get("y", 0.0)), 0.0)
        alt = _to_float(row.get("altM", row.get("z", 0.0)), 0.0)
        out = dict(row)
        out["x"] = lon
        out["y"] = lat
        out["z"] = alt
        out["lon"] = lon
        out["lat"] = lat
        out["altM"] = alt
        return out

    def sync_plot_point(self, feature: Dict[str, Any], *, scope: str = "shared", source: str = "map") -> Dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        geometry = _as_dict(feature.get("geometry"))
        coordinates = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
        props = _as_dict(feature.get("properties"))
        point_id = str(props.get("id", "point")).strip() or "point"
        lon = _to_float(coordinates[0] if len(coordinates) > 0 else props.get("lon"), 0.0)
        lat = _to_float(coordinates[1] if len(coordinates) > 1 else props.get("lat"), 0.0)
        alt = _to_float(coordinates[2] if len(coordinates) > 2 else props.get("hae_m"), 0.0)
        row = {
            "id": point_id,
            "x": lon,
            "y": lat,
            "z": alt,
            "lon": lon,
            "lat": lat,
            "altM": alt,
            "metadata": _as_dict(props.get("metadata")),
            "updatedAt": _to_iso_now(),
        }
        with self._lock:
            scopes = self._load_scopes_locked()
            scope_state = self._ensure_scope_locked(scopes, normalized_scope)
            points = self._layer_rows(scope_state, "points")
            points[point_id] = row
            self._set_layer(scope_state, "points", points)
            self._save_scopes_locked(scopes)
            return self._record_event_locked(
                scope=normalized_scope,
                source=source,
                topic="plot-point",
                payload={"pointId": point_id},
            )

    def sync_network_state(self, state: Dict[str, Any], *, scope: str = "shared", source: str = "network") -> Dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        root = _as_dict(state.get("result")) if isinstance(state.get("result"), dict) else _as_dict(state)

        raw_base = _as_list_of_dicts(root.get("baseStations", root.get("base_stations")))
        raw_coverage = _as_list_of_dicts(root.get("coverage"))
        raw_tracks = _as_list_of_dicts(root.get("trackingSnapshots", root.get("tracking_snapshots")))
        raw_uavs = _as_list_of_dicts(root.get("uavs"))
        tracked_uavs = _as_dict(root.get("tracked_uavs"))
        if not raw_uavs and tracked_uavs:
            raw_uavs = [dict(v) for v in tracked_uavs.values() if isinstance(v, dict)]
        if not raw_tracks and raw_uavs:
            synthesized_tracks: list[Dict[str, Any]] = []
            for row in raw_uavs:
                pos = _as_dict(row.get("position"))
                if not pos:
                    continue
                synthesized = dict(pos)
                synthesized["id"] = str(row.get("id", row.get("uav_id", "")))
                synthesized["speedMps"] = row.get("speedMps", row.get("speed_mps", 0.0))
                synthesized_tracks.append(synthesized)
            raw_tracks = synthesized_tracks
        utm = _as_dict(root.get("utm"))
        raw_nfz = _as_list_of_dicts(utm.get("noFlyZones", utm.get("no_fly_zones")))

        base_rows = [self._normalize_xy(row) for row in raw_base]
        coverage_rows = []
        for row in raw_coverage:
            bs_id = str(row.get("bsId", row.get("bs_id", ""))).strip()
            if not bs_id:
                continue
            coverage_rows.append(
                {
                    "bsId": bs_id,
                    "radiusM": _to_float(row.get("radiusM", row.get("radius_m", 0.0)), 0.0),
                }
            )
        track_rows = [self._normalize_xy(row) for row in raw_tracks]
        nfz_rows = []
        for row in raw_nfz:
            zone_id = str(row.get("zone_id", row.get("id", ""))).strip()
            nfz_rows.append(
                {
                    "zone_id": zone_id or f"nfz-{len(nfz_rows) + 1}",
                    "cx": _to_float(row.get("cx", row.get("lon", row.get("x", 0.0))), 0.0),
                    "cy": _to_float(row.get("cy", row.get("lat", row.get("y", 0.0))), 0.0),
                    "radius_m": _to_float(row.get("radius_m", row.get("radiusM", 0.0)), 0.0),
                    "z_min": _to_float(row.get("z_min", 0.0), 0.0),
                    "z_max": _to_float(row.get("z_max", 120.0), 120.0),
                    "shape": str(row.get("shape", "circle")),
                    "reason": str(row.get("reason", "")),
                }
            )

        path_rows = []
        for row in raw_uavs:
            uav_id = str(row.get("id", row.get("uav_id", ""))).strip()
            route = _as_list_of_dicts(row.get("route"))
            if not uav_id or not route:
                continue
            path_rows.append(
                {
                    "id": f"{uav_id}:path",
                    "uavId": uav_id,
                    "route": [self._normalize_xy(p) for p in route],
                    "source": "network",
                }
            )

        with self._lock:
            scopes = self._load_scopes_locked()
            scope_state = self._ensure_scope_locked(scopes, normalized_scope)
            self._set_layer(scope_state, "baseStations", self._rows_to_layer_map(base_rows, key_candidates=("id",)))
            self._set_layer(scope_state, "coverage", self._rows_to_layer_map(coverage_rows, key_candidates=("bsId",)))
            self._set_layer(scope_state, "uavs", self._rows_to_layer_map(track_rows, key_candidates=("id",)))
            if path_rows:
                self._set_layer(scope_state, "paths", self._rows_to_layer_map(path_rows, key_candidates=("id",)))
            if nfz_rows:
                self._set_layer(scope_state, "noFlyZones", self._rows_to_layer_map(nfz_rows, key_candidates=("zone_id", "id")))
            self._save_scopes_locked(scopes)
            return self._record_event_locked(
                scope=normalized_scope,
                source=source,
                topic="network-state",
                payload={
                    "baseStations": len(base_rows),
                    "coverage": len(coverage_rows),
                    "uavs": len(track_rows),
                    "paths": len(path_rows),
                    "noFlyZones": len(nfz_rows),
                },
            )

    def sync_utm_state(self, state: Dict[str, Any], *, scope: str = "shared", source: str = "utm") -> Dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        root = _as_dict(state.get("result")) if isinstance(state.get("result"), dict) else _as_dict(state)
        raw_nfz = _as_list_of_dicts(
            root.get("noFlyZones", root.get("no_fly_zones", root.get("no_fly_zone", root.get("noFlyZone"))))
        )
        if not raw_nfz and isinstance(root.get("weather_by_airspace"), dict):
            # Handles UTM_SERVICE.export_state() shape.
            raw_nfz = _as_list_of_dicts(root.get("no_fly_zones"))

        nfz_rows = []
        for row in raw_nfz:
            zone_id = str(row.get("zone_id", row.get("id", ""))).strip()
            nfz_rows.append(
                {
                    "zone_id": zone_id or f"nfz-{len(nfz_rows) + 1}",
                    "cx": _to_float(row.get("cx", row.get("lon", row.get("x", 0.0))), 0.0),
                    "cy": _to_float(row.get("cy", row.get("lat", row.get("y", 0.0))), 0.0),
                    "radius_m": _to_float(row.get("radius_m", row.get("radiusM", 0.0)), 0.0),
                    "z_min": _to_float(row.get("z_min", 0.0), 0.0),
                    "z_max": _to_float(row.get("z_max", 120.0), 120.0),
                    "shape": str(row.get("shape", "circle")),
                    "reason": str(row.get("reason", "")),
                }
            )

        with self._lock:
            scopes = self._load_scopes_locked()
            scope_state = self._ensure_scope_locked(scopes, normalized_scope)
            self._set_layer(scope_state, "noFlyZones", self._rows_to_layer_map(nfz_rows, key_candidates=("zone_id", "id")))
            self._save_scopes_locked(scopes)
            return self._record_event_locked(
                scope=normalized_scope,
                source=source,
                topic="utm-state",
                payload={"noFlyZones": len(nfz_rows)},
            )

    def sync_uav_fleet(self, fleet_or_state: Dict[str, Any], *, scope: str = "shared", source: str = "uav") -> Dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        root = _as_dict(fleet_or_state.get("result")) if isinstance(fleet_or_state.get("result"), dict) else _as_dict(fleet_or_state)
        fleet = _as_dict(root.get("fleet")) if isinstance(root.get("fleet"), dict) else root

        track_rows = []
        path_rows = []
        for uav_id, row_any in fleet.items():
            row = _as_dict(row_any)
            if not row:
                continue
            pos = self._normalize_xy(_as_dict(row.get("position")))
            track_rows.append(
                {
                    "id": str(uav_id),
                    "x": pos["x"],
                    "y": pos["y"],
                    "z": pos["z"],
                    "speedMps": _to_float(row.get("velocity_mps", row.get("speedMps", 0.0)), 0.0),
                    "headingDeg": _to_float(row.get("headingDeg", 0.0), 0.0),
                    "attachedBsId": str(row.get("attachedBsId", "")),
                    "interferenceRisk": str(row.get("interferenceRisk", "")),
                }
            )
            route = _as_list_of_dicts(row.get("waypoints"))
            if route:
                path_rows.append(
                    {
                        "id": f"{uav_id}:path",
                        "uavId": str(uav_id),
                        "route": [self._normalize_xy(wp) for wp in route],
                        "source": source,
                    }
                )

        with self._lock:
            scopes = self._load_scopes_locked()
            scope_state = self._ensure_scope_locked(scopes, normalized_scope)
            self._set_layer(scope_state, "uavs", self._rows_to_layer_map(track_rows, key_candidates=("id",)))
            self._set_layer(scope_state, "paths", self._rows_to_layer_map(path_rows, key_candidates=("id",)))
            self._save_scopes_locked(scopes)
            return self._record_event_locked(
                scope=normalized_scope,
                source=source,
                topic="uav-fleet",
                payload={"uavs": len(track_rows), "paths": len(path_rows)},
            )

    def publish(
        self,
        *,
        source: str,
        topic: str,
        payload: Dict[str, Any],
        scope: str = "shared",
    ) -> Dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        with self._lock:
            scopes = self._load_scopes_locked()
            self._ensure_scope_locked(scopes, normalized_scope)
            self._save_scopes_locked(scopes)
            return self._record_event_locked(
                scope=normalized_scope,
                source=str(source or "unknown"),
                topic=str(topic or "custom"),
                payload=dict(payload),
            )

    @staticmethod
    def _merge_layer_maps(shared_rows: Dict[str, Any], scoped_rows: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(shared_rows)
        out.update(scoped_rows)
        return out

    def snapshot(self, *, scope: str = "shared", include_shared: bool = True) -> Dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        with self._lock:
            scopes = self._load_scopes_locked()
            target = self._ensure_scope_locked(scopes, normalized_scope)
            shared = self._ensure_scope_locked(scopes, "shared")

            target_layers = _as_dict(target.get("layers"))
            shared_layers = _as_dict(shared.get("layers"))
            out_layers: Dict[str, list[Dict[str, Any]]] = {}
            for layer in ("uavs", "paths", "noFlyZones", "baseStations", "coverage", "points"):
                scoped_rows = _as_dict(target_layers.get(layer))
                if include_shared and normalized_scope != "shared":
                    merged_rows = self._merge_layer_maps(_as_dict(shared_layers.get(layer)), scoped_rows)
                else:
                    merged_rows = scoped_rows
                out_layers[layer] = [dict(row) for _k, row in sorted(merged_rows.items(), key=lambda kv: kv[0])]
            sync = self._db.get_sync()
            return {
                "scope": normalized_scope,
                "includeShared": bool(include_shared),
                "sharedScope": "shared",
                "availableScopes": sorted(scopes.keys()),
                "updatedAt": str(target.get("updatedAt", _to_iso_now())),
                "sync": sync,
                "layers": out_layers,
            }

    def events(self, *, scope: str | None = None, since_id: int = 0, limit: int = 100) -> Dict[str, Any]:
        wanted_scope = _normalize_scope(scope) if isinstance(scope, str) and scope.strip() else None
        min_id = max(0, int(_to_float(since_id, 0.0)))
        lim = max(1, min(500, int(_to_float(limit, 100.0))))
        with self._lock:
            events_raw = self._db.get_state(self._STATE_EVENTS_KEY)
            events = [dict(row) for row in events_raw if isinstance(row, dict)] if isinstance(events_raw, list) else []
            filtered: list[Dict[str, Any]] = []
            for row in events:
                row_id = int(_to_float(row.get("id"), 0.0))
                if row_id <= min_id:
                    continue
                if wanted_scope and str(row.get("scope")) != wanted_scope:
                    continue
                filtered.append(row)
            filtered = filtered[-lim:]
            last_id = int(_to_float(filtered[-1].get("id"), min_id)) if filtered else min_id
            return {
                "scope": wanted_scope,
                "sinceId": min_id,
                "lastEventId": last_id,
                "count": len(filtered),
                "events": filtered,
                "sync": self._db.get_sync(),
            }


class MapServiceAgent:
    def __init__(self, fetcher: MapDataFetcher | None = None) -> None:
        self._fetcher = fetcher or MAP_DATA_FETCHER
        cfg = load_map_agent_config()
        defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
        configured = str(defaults.get("view_engine", DEFAULT_ENGINE_ID) or DEFAULT_ENGINE_ID)
        self._view_engine = configured if configured in supported_engine_ids() else DEFAULT_ENGINE_ID
        self._max_plot_points = int(_to_float(defaults.get("max_plot_points", 5000), 5000.0))
        self._points: Dict[str, Dict[str, Any]] = {}
        self._sync_broker = MapSyncBroker(max_events=int(_to_float(defaults.get("max_sync_events", 2000), 2000.0)))

    def _trim_point_cache(self) -> None:
        overflow = len(self._points) - max(1, self._max_plot_points)
        if overflow <= 0:
            return
        # Preserve insertion order and remove oldest entries first.
        for key in list(self._points.keys())[:overflow]:
            self._points.pop(key, None)

    def plot_point(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        feature = gps_to_geojson_feature(payload)
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        point_id = str(props.get("id", payload.get("id", "point")))
        self._points[point_id] = feature
        self._trim_point_cache()
        self._sync_broker.sync_plot_point(feature, scope=str(_as_dict(payload).get("scope", "shared")))
        return {
            "pointId": point_id,
            "feature": feature,
            "pointCount": len(self._points),
        }

    def current_bounds(self) -> Dict[str, Any]:
        if self._points:
            lon_vals = []
            lat_vals = []
            for feature in self._points.values():
                geom = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
                coords = geom.get("coordinates") if isinstance(geom.get("coordinates"), list) else []
                if len(coords) >= 2:
                    lon_vals.append(_to_float(coords[0], 0.0))
                    lat_vals.append(_to_float(coords[1], 0.0))
            if lon_vals and lat_vals:
                return {
                    "west": round(min(lon_vals), 8),
                    "south": round(min(lat_vals), 8),
                    "east": round(max(lon_vals), 8),
                    "north": round(max(lat_vals), 8),
                    "pointCount": len(self._points),
                    "source": "plotted-points",
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }

        cfg = load_map_agent_config()
        b = cfg.get("finland_bounds") if isinstance(cfg.get("finland_bounds"), dict) else {}
        return {
            "west": _to_float(b.get("min_lon", 19.0), 19.0),
            "south": _to_float(b.get("min_lat", 59.0), 59.0),
            "east": _to_float(b.get("max_lon", 32.0), 32.0),
            "north": _to_float(b.get("max_lat", 70.5), 70.5),
            "pointCount": 0,
            "source": "finland-default",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    def toggle_view(self, engine: str) -> Dict[str, Any]:
        target = str(engine or "").strip()
        if target not in supported_engine_ids():
            raise ValueError(f"unsupported_engine:{target}")
        prev = self._view_engine
        self._view_engine = target
        return {
            "previousEngine": prev,
            "activeEngine": self._view_engine,
            "supportedEngines": supported_engines(),
        }

    def view_status(self) -> Dict[str, Any]:
        return {
            "activeEngine": self._view_engine,
            "supportedEngines": supported_engines(),
        }

    def public_config(self, base_url: str) -> Dict[str, Any]:
        return self._fetcher.public_config(
            base_url,
            view_engine=self._view_engine,
            supported_engines=supported_engines(),
        )

    def cache_status(self) -> Dict[str, Any]:
        return self._fetcher.cache_status()

    def prefetch(
        self,
        *,
        provider: str = "all",
        center_lon: float | None = None,
        center_lat: float | None = None,
        radius_km: float | None = None,
        zoom_min: int | None = None,
        zoom_max: int | None = None,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        return self._fetcher.prefetch(
            provider=provider,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_km=radius_km,
            zoom_min=zoom_min,
            zoom_max=zoom_max,
            force_refresh=force_refresh,
        )

    def get_tile(self, provider: str, z: int, x: int, y: int) -> Dict[str, Any]:
        return self._fetcher.get_tile(provider=provider, z=z, x=x, y=y)

    def sync_publish(
        self,
        *,
        source: str,
        topic: str,
        payload: Dict[str, Any],
        scope: str = "shared",
    ) -> Dict[str, Any]:
        return self._sync_broker.publish(source=source, topic=topic, payload=payload, scope=scope)

    def sync_network_state(self, state: Dict[str, Any], *, scope: str = "shared", source: str = "network") -> Dict[str, Any]:
        return self._sync_broker.sync_network_state(state, scope=scope, source=source)

    def sync_utm_state(self, state: Dict[str, Any], *, scope: str = "shared", source: str = "utm") -> Dict[str, Any]:
        return self._sync_broker.sync_utm_state(state, scope=scope, source=source)

    def sync_uav_fleet(self, fleet_or_state: Dict[str, Any], *, scope: str = "shared", source: str = "uav") -> Dict[str, Any]:
        return self._sync_broker.sync_uav_fleet(fleet_or_state, scope=scope, source=source)

    def sync_state(self, *, scope: str = "shared", include_shared: bool = True) -> Dict[str, Any]:
        return self._sync_broker.snapshot(scope=scope, include_shared=bool(include_shared))

    def sync_events(self, *, scope: str | None = None, since_id: int = 0, limit: int = 100) -> Dict[str, Any]:
        return self._sync_broker.events(scope=scope, since_id=since_id, limit=limit)


MAP_SERVICE_AGENT = MapServiceAgent()
