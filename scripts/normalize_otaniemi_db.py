#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Tuple


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


def _load_geo_utils():
    import sys

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from geo_utils import haversine_m, is_valid_lon_lat, lon_lat_from_local_xy_m

    return haversine_m, is_valid_lon_lat, lon_lat_from_local_xy_m


HAVERSINE_M, IS_VALID_LON_LAT, LON_LAT_FROM_LOCAL_XY_M = _load_geo_utils()

OTANIEMI_CENTER_LON = 24.8286
OTANIEMI_CENTER_LAT = 60.1866
LEGACY_LOCAL_MAX_X_M = 2000.0
LEGACY_LOCAL_MAX_Y_M = 2000.0
MAP_RELEVANCE_RADIUS_M = 20000.0


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        return float(value)
    except Exception:
        return None


def _coord_scalar(row: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, (list, tuple, dict)):
            continue
        parsed = _as_float(value)
        if parsed is not None:
            return parsed
    return None


def _looks_local_xy(x: float, y: float) -> bool:
    return 0.0 <= float(x) <= LEGACY_LOCAL_MAX_X_M and 0.0 <= float(y) <= LEGACY_LOCAL_MAX_Y_M


def _coerce_lon_lat(lon: float, lat: float) -> Tuple[float, float]:
    x = float(lon)
    y = float(lat)
    if IS_VALID_LON_LAT(x, y):
        if HAVERSINE_M(x, y, OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT) <= MAP_RELEVANCE_RADIUS_M:
            return x, y
        if _looks_local_xy(x, y):
            return LON_LAT_FROM_LOCAL_XY_M(
                x,
                y,
                ref_lon=OTANIEMI_CENTER_LON,
                ref_lat=OTANIEMI_CENTER_LAT,
            )
        return OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT
    if _looks_local_xy(x, y):
        return LON_LAT_FROM_LOCAL_XY_M(
            x,
            y,
            ref_lon=OTANIEMI_CENTER_LON,
            ref_lat=OTANIEMI_CENTER_LAT,
        )
    return OTANIEMI_CENTER_LON, OTANIEMI_CENTER_LAT


def _normalize_point_like(row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    out = dict(row)
    changed = False
    lon = _coord_scalar(out, ("lon", "x", "lng", "longitude"))
    lat = _coord_scalar(out, ("lat", "y", "latitude"))
    if lon is not None and lat is not None:
        lon_new, lat_new = _coerce_lon_lat(lon, lat)
        if abs(lon_new - lon) > 1e-12 or abs(lat_new - lat) > 1e-12:
            changed = True
        out["lon"] = float(lon_new)
        out["lat"] = float(lat_new)
        out["x"] = float(lon_new)
        out["y"] = float(lat_new)
        alt = _coord_scalar(out, ("altM", "z", "alt_m", "altitude_m", "altitude"))
        if alt is not None:
            alt_new = max(0.0, float(alt))
            if abs(alt_new - alt) > 1e-12:
                changed = True
            out["altM"] = float(alt_new)
            out["z"] = float(alt_new)

    cx = _coord_scalar(out, ("cx", "lon", "x"))
    cy = _coord_scalar(out, ("cy", "lat", "y"))
    if cx is not None and cy is not None and ("radius_m" in out or "radiusM" in out or "z_min" in out or "z_max" in out):
        lon_new, lat_new = _coerce_lon_lat(cx, cy)
        if abs(lon_new - cx) > 1e-12 or abs(lat_new - cy) > 1e-12:
            changed = True
        out["cx"] = float(lon_new)
        out["cy"] = float(lat_new)
        out["lon"] = float(lon_new)
        out["lat"] = float(lat_new)
    return out, changed


def _normalize_nested(value: Any) -> tuple[Any, bool]:
    if isinstance(value, list):
        any_changed = False
        out_list = []
        for item in value:
            item_out, changed = _normalize_nested(item)
            out_list.append(item_out)
            any_changed = any_changed or changed
        return out_list, any_changed
    if isinstance(value, dict):
        any_changed = False
        out_map: dict[str, Any] = {}
        for key, item in value.items():
            item_out, changed = _normalize_nested(item)
            out_map[str(key)] = item_out
            any_changed = any_changed or changed
        out_map, changed_point = _normalize_point_like(out_map)
        any_changed = any_changed or changed_point
        return out_map, any_changed
    return value, False


def _default_db_path() -> Path:
    return BACKEND_DIR / "data" / "agents.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize persisted UAV/UTM/Network DB coordinates to Aalto Otaniemi.")
    parser.add_argument("--db", type=Path, default=_default_db_path(), help="Path to agents sqlite DB")
    parser.add_argument("--agents", nargs="+", default=["uav", "utm", "network"], help="Agent namespaces to normalize")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report only, do not write")
    parser.add_argument("--no-backup", action="store_true", help="Skip DB backup before write")
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    if not args.dry_run and not args.no_backup:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = db_path.with_name(f"{db_path.name}.{stamp}.bak")
        shutil.copy2(db_path, backup_path)
        print(f"backup: {backup_path}")

    agents = tuple(str(a).strip() for a in args.agents if str(a).strip())
    if not agents:
        print("No agents selected; exiting.")
        return 0

    now = _utc_now()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    changed_rows = 0
    changed_agents: dict[str, int] = {}
    scanned_rows = 0
    try:
        marks = ",".join("?" for _ in agents)
        rows = conn.execute(
            f"SELECT agent, state_key, value_json FROM agent_state WHERE agent IN ({marks}) ORDER BY agent, state_key",
            agents,
        ).fetchall()
        for row in rows:
            scanned_rows += 1
            agent = str(row["agent"])
            state_key = str(row["state_key"])
            try:
                data = json.loads(str(row["value_json"]))
            except Exception:
                continue
            normalized, changed = _normalize_nested(data)
            if not changed:
                continue
            changed_rows += 1
            changed_agents[agent] = changed_agents.get(agent, 0) + 1
            print(f"normalized: {agent}:{state_key}")
            if args.dry_run:
                continue
            raw = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
            conn.execute(
                """
                UPDATE agent_state
                   SET value_json = ?, updated_at = ?
                 WHERE agent = ? AND state_key = ?
                """,
                (raw, now, agent, state_key),
            )
        if not args.dry_run:
            for agent in changed_agents:
                row = conn.execute("SELECT 1 FROM agent_meta WHERE agent = ?", (agent,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO agent_meta(agent, revision, updated_at) VALUES (?, 1, ?)",
                        (agent, now),
                    )
                else:
                    conn.execute(
                        "UPDATE agent_meta SET revision = revision + 1, updated_at = ? WHERE agent = ?",
                        (now, agent),
                    )
            conn.commit()
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "db": str(db_path),
                "agents": list(agents),
                "scanned_rows": scanned_rows,
                "changed_rows": changed_rows,
                "changed_agents": changed_agents,
                "dry_run": bool(args.dry_run),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

