from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any, Dict


_CONFIG_PATH = Path(__file__).with_name("config.json")

_DEFAULTS: Dict[str, Any] = {
    "nls": {
        "api_key_env": "NLS_API_KEY",
        "wmts_base_url": "https://avoin-karttakuva.maanmittauslaitos.fi/avoin/wmts/1.0.0",
    },
    "finland_bounds": {
        "min_lon": 19.0,
        "max_lon": 32.0,
        "min_lat": 59.0,
        "max_lat": 70.5,
    },
    "defaults": {
        "center_lon": 24.8286,
        "center_lat": 60.1866,
        "radius_km": 10.0,
        "zoom_min": 11,
        "zoom_max": 15,
        "view_engine": "2D_Leaflet",
        "max_plot_points": 5000,
    },
    "altitude": {
        "default_geoid_separation_m": 0.0,
    },
}


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def load_map_agent_config() -> Dict[str, Any]:
    cfg = deepcopy(_DEFAULTS)
    if _CONFIG_PATH.exists():
        try:
            loaded = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                _deep_merge(cfg, loaded)
        except Exception:
            # Keep runtime-safe defaults if config file is malformed.
            pass
    return cfg


def get_nls_api_key(config: Dict[str, Any] | None = None) -> str:
    cfg = config or load_map_agent_config()
    nls_cfg = cfg.get("nls") if isinstance(cfg.get("nls"), dict) else {}
    env_name = str(nls_cfg.get("api_key_env", "NLS_API_KEY") or "NLS_API_KEY")
    return str(os.getenv(env_name, "") or "").strip()
