from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Tuple


EARTH_RADIUS_M = 6_371_008.8
METERS_PER_DEG_LAT = 111_320.0
WGS84_A = 6_378_137.0
WGS84_F = 1.0 / 298.257_223_563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
WGS84_B = WGS84_A * (1.0 - WGS84_F)
WGS84_EP2 = (WGS84_A * WGS84_A - WGS84_B * WGS84_B) / (WGS84_B * WGS84_B)


def _deg_to_rad(deg: float) -> float:
    return (float(deg) * math.pi) / 180.0


def _rad_to_deg(rad: float) -> float:
    return (float(rad) * 180.0) / math.pi


def geodetic_to_ecef(lon: float, lat: float, alt_m: float = 0.0) -> Tuple[float, float, float]:
    lon_r = _deg_to_rad(lon)
    lat_r = _deg_to_rad(lat)
    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    sin_lon = math.sin(lon_r)
    cos_lon = math.cos(lon_r)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + float(alt_m)) * cos_lat * cos_lon
    y = (n + float(alt_m)) * cos_lat * sin_lon
    z = (n * (1.0 - WGS84_E2) + float(alt_m)) * sin_lat
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    p = math.hypot(float(x), float(y))
    if p < 1e-9:
        lat = 90.0 if float(z) >= 0.0 else -90.0
        alt_m = abs(float(z)) - WGS84_B
        return 0.0, lat, alt_m
    lon = math.atan2(float(y), float(x))
    theta = math.atan2(float(z) * WGS84_A, p * WGS84_B)
    sin_theta = math.sin(theta)
    cos_theta = math.cos(theta)
    lat = math.atan2(
        float(z) + WGS84_EP2 * WGS84_B * sin_theta * sin_theta * sin_theta,
        p - WGS84_E2 * WGS84_A * cos_theta * cos_theta * cos_theta,
    )
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    if abs(cos_lat) < 1e-12:
        alt_m = float(z) / max(1e-12, sin_lat) - n * (1.0 - WGS84_E2)
    else:
        alt_m = p / cos_lat - n
    return _rad_to_deg(lon), _rad_to_deg(lat), alt_m


def ecef_to_enu(
    x: float,
    y: float,
    z: float,
    *,
    ref_lon: float,
    ref_lat: float,
    ref_alt_m: float = 0.0,
) -> Tuple[float, float, float]:
    ref_x, ref_y, ref_z = geodetic_to_ecef(ref_lon, ref_lat, ref_alt_m)
    dx = float(x) - ref_x
    dy = float(y) - ref_y
    dz = float(z) - ref_z
    lon0 = _deg_to_rad(ref_lon)
    lat0 = _deg_to_rad(ref_lat)
    sin_lon = math.sin(lon0)
    cos_lon = math.cos(lon0)
    sin_lat = math.sin(lat0)
    cos_lat = math.cos(lat0)
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    return east, north, up


def enu_to_ecef(
    east_m: float,
    north_m: float,
    up_m: float,
    *,
    ref_lon: float,
    ref_lat: float,
    ref_alt_m: float = 0.0,
) -> Tuple[float, float, float]:
    ref_x, ref_y, ref_z = geodetic_to_ecef(ref_lon, ref_lat, ref_alt_m)
    lon0 = _deg_to_rad(ref_lon)
    lat0 = _deg_to_rad(ref_lat)
    sin_lon = math.sin(lon0)
    cos_lon = math.cos(lon0)
    sin_lat = math.sin(lat0)
    cos_lat = math.cos(lat0)
    dx = -sin_lon * float(east_m) - sin_lat * cos_lon * float(north_m) + cos_lat * cos_lon * float(up_m)
    dy = cos_lon * float(east_m) - sin_lat * sin_lon * float(north_m) + cos_lat * sin_lon * float(up_m)
    dz = cos_lat * float(north_m) + sin_lat * float(up_m)
    return ref_x + dx, ref_y + dy, ref_z + dz


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def is_valid_lon_lat(lon: float, lat: float) -> bool:
    return -180.0 <= float(lon) <= 180.0 and -90.0 <= float(lat) <= 90.0


def extract_lon_lat_alt(point: Dict[str, Any] | None, *, default_alt: float = 0.0) -> Tuple[float, float, float]:
    row = point if isinstance(point, dict) else {}
    lon = as_float(
        row.get("lon", row.get("x", row.get("lng", row.get("longitude", 0.0)))),
        0.0,
    )
    lat = as_float(
        row.get("lat", row.get("y", row.get("latitude", 0.0))),
        0.0,
    )
    alt_m = as_float(
        row.get("altM", row.get("z", row.get("alt_m", row.get("altitude_m", default_alt)))),
        default_alt,
    )
    return lon, lat, alt_m


def point_with_aliases(point: Dict[str, Any] | None, *, default_alt: float = 0.0) -> Dict[str, Any]:
    row = dict(point) if isinstance(point, dict) else {}
    lon, lat, alt_m = extract_lon_lat_alt(row, default_alt=default_alt)
    row["lon"] = float(lon)
    row["lat"] = float(lat)
    row["altM"] = float(alt_m)
    row["x"] = float(lon)
    row["y"] = float(lat)
    row["z"] = float(alt_m)
    return row


def normalize_waypoints(waypoints: Iterable[Any] | None) -> List[Dict[str, Any]]:
    if waypoints is None:
        return []
    out: List[Dict[str, Any]] = []
    for row in waypoints:
        if not isinstance(row, dict):
            continue
        out.append(point_with_aliases(row))
    return out


def extract_zone_center(zone: Dict[str, Any] | None) -> Tuple[float, float]:
    row = zone if isinstance(zone, dict) else {}
    lon = as_float(row.get("lon", row.get("cx", row.get("x", 0.0))), 0.0)
    lat = as_float(row.get("lat", row.get("cy", row.get("y", 0.0))), 0.0)
    return lon, lat


def zone_with_aliases(zone: Dict[str, Any] | None) -> Dict[str, Any]:
    row = dict(zone) if isinstance(zone, dict) else {}
    lon, lat = extract_zone_center(row)
    row["lon"] = float(lon)
    row["lat"] = float(lat)
    row["cx"] = float(lon)
    row["cy"] = float(lat)
    row["z_min"] = as_float(row.get("z_min", row.get("alt_min_m", 0.0)), 0.0)
    row["z_max"] = as_float(row.get("z_max", row.get("alt_max_m", 120.0)), 120.0)
    row["radius_m"] = as_float(row.get("radius_m", row.get("radiusM", 0.0)), 0.0)
    shape_raw = str(row.get("shape", "circle")).strip().lower()
    row["shape"] = "circle" if shape_raw == "circle" else "box"
    return row


def normalize_no_fly_zones(zones: Iterable[Any] | None) -> List[Dict[str, Any]]:
    if zones is None:
        return []
    out: List[Dict[str, Any]] = []
    for row in zones:
        if not isinstance(row, dict):
            continue
        out.append(zone_with_aliases(row))
    return out


def looks_like_geo_point(point: Dict[str, Any] | None) -> bool:
    if not isinstance(point, dict):
        return False
    lon, lat, _alt = extract_lon_lat_alt(point)
    return is_valid_lon_lat(lon, lat)


def looks_like_geo_waypoints(waypoints: Iterable[Any] | None) -> bool:
    if waypoints is None:
        return False
    rows = [w for w in waypoints if isinstance(w, dict)]
    if not rows:
        return False
    return all(looks_like_geo_point(w) for w in rows)


def haversine_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    lon1 = math.radians(float(lon_a))
    lat1 = math.radians(float(lat_a))
    lon2 = math.radians(float(lon_b))
    lat2 = math.radians(float(lat_b))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    sin_dlat = math.sin(dlat / 2.0)
    sin_dlon = math.sin(dlon / 2.0)
    h = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    c = 2.0 * math.atan2(math.sqrt(max(0.0, h)), math.sqrt(max(0.0, 1.0 - h)))
    return EARTH_RADIUS_M * c


def horizontal_distance_m(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax, ay, _az = extract_lon_lat_alt(a)
    bx, by, _bz = extract_lon_lat_alt(b)
    if is_valid_lon_lat(ax, ay) and is_valid_lon_lat(bx, by):
        return haversine_m(ax, ay, bx, by)
    return math.hypot(bx - ax, by - ay)


def distance_3d_m(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    _, _, az = extract_lon_lat_alt(a)
    _, _, bz = extract_lon_lat_alt(b)
    dxy = horizontal_distance_m(a, b)
    dz = float(bz) - float(az)
    return math.hypot(dxy, dz)


def local_xy_m_from_lon_lat(lon: float, lat: float, *, ref_lon: float, ref_lat: float) -> Tuple[float, float]:
    if is_valid_lon_lat(lon, lat) and is_valid_lon_lat(ref_lon, ref_lat):
        gx, gy, gz = geodetic_to_ecef(float(lon), float(lat), 0.0)
        east_m, north_m, _up_m = ecef_to_enu(
            gx,
            gy,
            gz,
            ref_lon=float(ref_lon),
            ref_lat=float(ref_lat),
            ref_alt_m=0.0,
        )
        return east_m, north_m
    meters_per_deg_lon = METERS_PER_DEG_LAT * math.cos(math.radians(float(ref_lat)))
    x_m = (float(lon) - float(ref_lon)) * meters_per_deg_lon
    y_m = (float(lat) - float(ref_lat)) * METERS_PER_DEG_LAT
    return x_m, y_m


def lon_lat_from_local_xy_m(x_m: float, y_m: float, *, ref_lon: float, ref_lat: float) -> Tuple[float, float]:
    if is_valid_lon_lat(ref_lon, ref_lat):
        gx, gy, gz = enu_to_ecef(
            float(x_m),
            float(y_m),
            0.0,
            ref_lon=float(ref_lon),
            ref_lat=float(ref_lat),
            ref_alt_m=0.0,
        )
        lon, lat, _alt = ecef_to_geodetic(gx, gy, gz)
        return lon, lat
    meters_per_deg_lon = METERS_PER_DEG_LAT * math.cos(math.radians(float(ref_lat)))
    lon = float(ref_lon) + (float(x_m) / meters_per_deg_lon if abs(meters_per_deg_lon) > 1e-9 else 0.0)
    lat = float(ref_lat) + float(y_m) / METERS_PER_DEG_LAT
    return lon, lat


def local_xy_m(point: Dict[str, Any], *, ref_lon: float, ref_lat: float) -> Tuple[float, float]:
    lon, lat, _alt = extract_lon_lat_alt(point)
    return local_xy_m_from_lon_lat(lon, lat, ref_lon=ref_lon, ref_lat=ref_lat)


def local_z_m(point: Dict[str, Any], *, default_alt: float = 0.0) -> float:
    _lon, _lat, alt = extract_lon_lat_alt(point, default_alt=default_alt)
    return float(alt)
