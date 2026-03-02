from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict

from geo_utils import geodetic_to_ecef, is_valid_lon_lat

from ..config import load_map_agent_config


WGS84_A = 6_378_137.0
WGS84_F = 1.0 / 298.257_223_563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
TM35FIN_K0 = 0.9996
TM35FIN_LON0_DEG = 27.0
TM35FIN_FALSE_EASTING = 500_000.0
TM35FIN_FALSE_NORTHING = 0.0


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _value_with_aliases(payload: Dict[str, Any], keys: tuple[str, ...], default: float = 0.0) -> float:
    for key in keys:
        if key in payload:
            return _to_float(payload.get(key), default)
    return default


def _wgs84_to_tm35fin(lon_deg: float, lat_deg: float) -> tuple[float, float]:
    """Project WGS84 lon/lat into ETRS89 / TM35FIN (EPSG:3067).

    Uses a standard transverse-mercator series expansion (same family used for UTM),
    but with TM35FIN parameters (lon0=27E, k0=0.9996, FE=500000, FN=0).
    """

    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    lon0 = math.radians(TM35FIN_LON0_DEG)

    e2 = WGS84_E2
    ep2 = e2 / (1.0 - e2)

    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    tan_lat = math.tan(lat)

    n = WGS84_A / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    t = tan_lat * tan_lat
    c = ep2 * cos_lat * cos_lat
    a = cos_lat * (lon - lon0)

    e4 = e2 * e2
    e6 = e4 * e2
    m = WGS84_A * (
        (1.0 - e2 / 4.0 - 3.0 * e4 / 64.0 - 5.0 * e6 / 256.0) * lat
        - (3.0 * e2 / 8.0 + 3.0 * e4 / 32.0 + 45.0 * e6 / 1024.0) * math.sin(2.0 * lat)
        + (15.0 * e4 / 256.0 + 45.0 * e6 / 1024.0) * math.sin(4.0 * lat)
        - (35.0 * e6 / 3072.0) * math.sin(6.0 * lat)
    )

    easting = TM35FIN_FALSE_EASTING + TM35FIN_K0 * n * (
        a
        + (1.0 - t + c) * (a**3) / 6.0
        + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * ep2) * (a**5) / 120.0
    )
    northing = TM35FIN_FALSE_NORTHING + TM35FIN_K0 * (
        m
        + n
        * tan_lat
        * (
            (a * a) / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * (a**4) / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * ep2) * (a**6) / 720.0
        )
    )

    return easting, northing


def gps_to_geojson_feature(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")

    cfg = load_map_agent_config()
    alt_cfg = cfg.get("altitude") if isinstance(cfg.get("altitude"), dict) else {}
    bounds_cfg = cfg.get("finland_bounds") if isinstance(cfg.get("finland_bounds"), dict) else {}

    point_id = str(payload.get("id", "point")).strip() or "point"
    lon = _value_with_aliases(payload, ("lon", "x", "lng", "longitude"))
    lat = _value_with_aliases(payload, ("lat", "y", "latitude"))
    alt_src_m = _value_with_aliases(payload, ("alt", "altM", "z", "alt_m", "altitude_m"), default=0.0)

    if not is_valid_lon_lat(lon, lat):
        raise ValueError("invalid_lon_lat")

    geoid_sep_m = payload.get("geoid_sep_m", payload.get("geoidSepM", None))
    if geoid_sep_m is None:
        geoid_sep_m = _to_float(alt_cfg.get("default_geoid_separation_m", 0.0), 0.0)
    else:
        geoid_sep_m = _to_float(geoid_sep_m, 0.0)

    alt_hae_m = float(alt_src_m) + float(geoid_sep_m)
    ecef_x, ecef_y, ecef_z = geodetic_to_ecef(lon, lat, alt_hae_m)
    tm35_e, tm35_n = _wgs84_to_tm35fin(lon, lat)

    in_finland = (
        _to_float(bounds_cfg.get("min_lon", 19.0), 19.0)
        <= lon
        <= _to_float(bounds_cfg.get("max_lon", 32.0), 32.0)
        and _to_float(bounds_cfg.get("min_lat", 59.0), 59.0)
        <= lat
        <= _to_float(bounds_cfg.get("max_lat", 70.5), 70.5)
    )

    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [round(lon, 8), round(lat, 8), round(alt_hae_m, 3)],
        },
        "properties": {
            "id": point_id,
            "sourceAltitudeM": round(float(alt_src_m), 3),
            "geoidSeparationM": round(float(geoid_sep_m), 3),
            "heightAboveEllipsoidM": round(float(alt_hae_m), 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "inFinlandBounds": bool(in_finland),
            "crs": {
                "wgs84": "EPSG:4979",
                "tm35fin": "EPSG:3067",
                "ecef": "EPSG:4978",
            },
            "tm35fin": {
                "easting": round(tm35_e, 3),
                "northing": round(tm35_n, 3),
            },
            "ecef": {
                "x": round(ecef_x, 3),
                "y": round(ecef_y, 3),
                "z": round(ecef_z, 3),
            },
        },
    }
