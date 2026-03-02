from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable

from ..config import get_nls_api_key, load_map_agent_config


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6_371_008.8
    a_lon = math.radians(lon1)
    a_lat = math.radians(lat1)
    b_lon = math.radians(lon2)
    b_lat = math.radians(lat2)
    d_lon = b_lon - a_lon
    d_lat = b_lat - a_lat
    h = math.sin(d_lat / 2) ** 2 + math.cos(a_lat) * math.cos(b_lat) * math.sin(d_lon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(max(0.0, h)), math.sqrt(max(0.0, 1.0 - h)))


def _tile_x(lon: float, z: int) -> int:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    return max(0, min(n - 1, x))


def _tile_y(lat: float, z: int) -> int:
    n = 2**z
    lat_rad = math.radians(_clamp(lat, -85.05112878, 85.05112878))
    y = int((1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, y))


def _tile_center_lon_lat(x: int, y: int, z: int) -> tuple[float, float]:
    n = 2**z
    lon = ((x + 0.5) / n) * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 0.5) / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    ext: str
    media_type: str
    max_zoom: int
    credit: str


class MapDataFetcher:
    def __init__(self) -> None:
        cfg = load_map_agent_config()
        defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}

        default_cache_dir = Path(__file__).resolve().parents[3] / "data" / "map_tiles"
        self.cache_root = Path(os.getenv("NETWORK_MAP_CACHE_DIR", str(default_cache_dir))).expanduser()
        self.cache_root.mkdir(parents=True, exist_ok=True)

        self.center_lon = _to_float(os.getenv("NETWORK_OTANIEMI_CENTER_LON", defaults.get("center_lon", 24.8286)), 24.8286)
        self.center_lat = _to_float(os.getenv("NETWORK_OTANIEMI_CENTER_LAT", defaults.get("center_lat", 60.1866)), 60.1866)
        self.default_radius_km = _to_float(os.getenv("NETWORK_MAP_PREFETCH_RADIUS_KM", defaults.get("radius_km", 10.0)), 10.0)
        self.default_zoom_min = _to_int(os.getenv("NETWORK_MAP_PREFETCH_ZOOM_MIN", defaults.get("zoom_min", 11)), 11)
        self.default_zoom_max = _to_int(os.getenv("NETWORK_MAP_PREFETCH_ZOOM_MAX", defaults.get("zoom_max", 15)), 15)

        self.prefetch_max_tiles = _to_int(os.getenv("NETWORK_MAP_PREFETCH_MAX_TILES", "12000"), 12000)
        self.timeout_s = _to_float(os.getenv("NETWORK_MAP_TILE_TIMEOUT_S", "3.0"), 3.0)

        nls_cfg = cfg.get("nls") if isinstance(cfg.get("nls"), dict) else {}
        self._nls_base = str(
            nls_cfg.get(
                "wmts_base_url",
                "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0",
            )
            or "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0"
        ).rstrip("/")
        self._nls_api_key = get_nls_api_key(cfg)

        self._providers: Dict[str, ProviderSpec] = {
            "osm": ProviderSpec(
                key="osm",
                label="OpenStreetMap",
                ext="png",
                media_type="image/png",
                max_zoom=19,
                credit="OpenStreetMap contributors",
            ),
            "nls-topo": ProviderSpec(
                key="nls-topo",
                label="NLS Topographic",
                ext="png",
                media_type="image/png",
                max_zoom=16,
                credit="Maanmittauslaitos (NLS Finland)",
            ),
            "nls-aerial": ProviderSpec(
                key="nls-aerial",
                label="NLS Aerial",
                ext="jpg",
                media_type="image/jpeg",
                max_zoom=16,
                credit="Maanmittauslaitos (NLS Finland)",
            ),
        }

    def _provider_spec(self, provider: str) -> ProviderSpec:
        key = str(provider or "").strip().lower()
        if key not in self._providers:
            raise ValueError(f"unsupported_provider:{provider}")
        return self._providers[key]

    def _provider_supported(self, provider: str) -> bool:
        spec = self._provider_spec(provider)
        if spec.key == "osm":
            return True
        return bool(self._nls_api_key)

    def _upstream_url(self, provider: str, z: int, x: int, y: int) -> str:
        spec = self._provider_spec(provider)
        if spec.key == "osm":
            return f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        if not self._nls_api_key:
            raise ValueError("nls_api_key_missing")
        layer = "taustakartta" if spec.key == "nls-topo" else "ortokuva"
        return (
            f"{self._nls_base}/{layer}/default/WGS84_Pseudo-Mercator/{z}/{y}/{x}.{spec.ext}"
            f"?api-key={urllib.parse.quote(self._nls_api_key)}"
        )

    def _tile_path(self, provider: str, z: int, x: int, y: int) -> Path:
        spec = self._provider_spec(provider)
        return self.cache_root / spec.key / str(z) / str(x) / f"{y}.{spec.ext}"

    def _fetch_upstream(self, url: str) -> tuple[bytes, str]:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "agent_test-map-agent/1.0 (+https://localhost)",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            content = resp.read()
            ctype = str(resp.headers.get("Content-Type", "")).split(";")[0].strip().lower()
        return content, ctype

    def get_tile(self, provider: str, z: int, x: int, y: int) -> Dict[str, Any]:
        spec = self._provider_spec(provider)
        if z < 0 or z > spec.max_zoom:
            raise ValueError(f"zoom_out_of_range:{z}")
        n = 2**z
        if x < 0 or x >= n or y < 0 or y >= n:
            raise ValueError("tile_out_of_range")
        if not self._provider_supported(spec.key):
            raise ValueError(f"provider_unavailable:{spec.key}")

        tile_path = self._tile_path(spec.key, z, x, y)
        if tile_path.exists():
            return {
                "content": tile_path.read_bytes(),
                "media_type": spec.media_type,
                "cache_hit": True,
                "provider": spec.key,
                "path": str(tile_path),
            }

        url = self._upstream_url(spec.key, z, x, y)
        content, upstream_type = self._fetch_upstream(url)
        tile_path.parent.mkdir(parents=True, exist_ok=True)
        tile_path.write_bytes(content)
        media = upstream_type or spec.media_type
        return {
            "content": content,
            "media_type": media,
            "cache_hit": False,
            "provider": spec.key,
            "path": str(tile_path),
        }

    def _prefetch_tiles_for_provider(
        self,
        provider: str,
        *,
        center_lon: float,
        center_lat: float,
        radius_km: float,
        zoom_min: int,
        zoom_max: int,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        spec = self._provider_spec(provider)
        if not self._provider_supported(spec.key):
            return {
                "provider": spec.key,
                "supported": False,
                "queued": 0,
                "fetched": 0,
                "hit": 0,
                "failed": 0,
                "error": "provider_unavailable",
            }

        radius_m = max(100.0, float(radius_km) * 1000.0)
        zoom_lo = max(0, min(spec.max_zoom, int(zoom_min)))
        zoom_hi = max(zoom_lo, min(spec.max_zoom, int(zoom_max)))
        queued = 0
        fetched = 0
        hit = 0
        failed = 0
        budget = max(1, int(self.prefetch_max_tiles))

        for z in range(zoom_lo, zoom_hi + 1):
            lat_delta = radius_m / 111_320.0
            lon_scale = max(1e-6, math.cos(math.radians(center_lat)))
            lon_delta = radius_m / (111_320.0 * lon_scale)
            lon_min = max(-180.0, center_lon - lon_delta)
            lon_max = min(180.0, center_lon + lon_delta)
            lat_min = max(-85.0511, center_lat - lat_delta)
            lat_max = min(85.0511, center_lat + lat_delta)
            x0 = _tile_x(lon_min, z)
            x1 = _tile_x(lon_max, z)
            y0 = _tile_y(lat_max, z)
            y1 = _tile_y(lat_min, z)
            if x1 < x0:
                x0, x1 = x1, x0
            if y1 < y0:
                y0, y1 = y1, y0

            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    if queued >= budget:
                        return {
                            "provider": spec.key,
                            "supported": True,
                            "queued": queued,
                            "fetched": fetched,
                            "hit": hit,
                            "failed": failed,
                            "truncated": True,
                        }
                    tile_lon, tile_lat = _tile_center_lon_lat(x, y, z)
                    if _haversine_m(center_lon, center_lat, tile_lon, tile_lat) > (radius_m * 1.15):
                        continue
                    queued += 1
                    tile_path = self._tile_path(spec.key, z, x, y)
                    if tile_path.exists() and not force_refresh:
                        hit += 1
                        continue
                    try:
                        info = self.get_tile(spec.key, z, x, y)
                        if info.get("cache_hit"):
                            hit += 1
                        else:
                            fetched += 1
                    except Exception:
                        failed += 1

        return {
            "provider": spec.key,
            "supported": True,
            "queued": queued,
            "fetched": fetched,
            "hit": hit,
            "failed": failed,
            "truncated": False,
        }

    def _provider_keys_for_prefetch(self, provider: str) -> Iterable[str]:
        key = str(provider or "all").strip().lower() or "all"
        if key == "all":
            return self._providers.keys()
        if key in self._providers:
            return [key]
        raise ValueError(f"unsupported_provider:{provider}")

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
        lon = float(center_lon if center_lon is not None else self.center_lon)
        lat = float(center_lat if center_lat is not None else self.center_lat)
        r_km = float(radius_km if radius_km is not None else self.default_radius_km)
        z_min = int(zoom_min if zoom_min is not None else self.default_zoom_min)
        z_max = int(zoom_max if zoom_max is not None else self.default_zoom_max)

        per_provider: Dict[str, Any] = {}
        for key in self._provider_keys_for_prefetch(provider):
            per_provider[key] = self._prefetch_tiles_for_provider(
                key,
                center_lon=lon,
                center_lat=lat,
                radius_km=r_km,
                zoom_min=z_min,
                zoom_max=z_max,
                force_refresh=bool(force_refresh),
            )
        return {
            "center": {"lon": lon, "lat": lat},
            "radiusKm": r_km,
            "zoomMin": z_min,
            "zoomMax": z_max,
            "providers": per_provider,
        }

    def cache_status(self) -> Dict[str, Any]:
        per_provider: Dict[str, int] = {}
        total = 0
        for key in self._providers:
            root = self.cache_root / key
            count = 0
            if root.exists():
                for p in root.rglob("*"):
                    if p.is_file():
                        count += 1
            per_provider[key] = count
            total += count
        return {"cacheRoot": str(self.cache_root), "tileCount": total, "perProvider": per_provider}

    def public_config(self, base_url: str, *, view_engine: str, supported_engines: list[Dict[str, Any]]) -> Dict[str, Any]:
        base = str(base_url or "").rstrip("/")
        providers: Dict[str, Dict[str, Any]] = {}
        for key, spec in self._providers.items():
            providers[key] = {
                "id": spec.key,
                "label": spec.label,
                "supported": self._provider_supported(spec.key),
                "maxZoom": spec.max_zoom,
                "credit": spec.credit,
                "tileUrlTemplate": f"{base}/api/map/tiles/{spec.key}/{{z}}/{{x}}/{{y}}",
            }
        return {
            "center": {"lon": self.center_lon, "lat": self.center_lat},
            "radiusKm": self.default_radius_km,
            "defaults": {
                "zoomMin": self.default_zoom_min,
                "zoomMax": self.default_zoom_max,
                "viewEngine": view_engine,
            },
            "providers": providers,
            "supportedEngines": supported_engines,
            "cacheStatus": self.cache_status(),
        }


MAP_DATA_FETCHER = MapDataFetcher()
