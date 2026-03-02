from __future__ import annotations

from typing import Any, Dict, List

from .cesium_3d import ENGINE_ID as CESIUM_ENGINE_ID
from .cesium_3d import descriptor as cesium_descriptor
from .leaflet_2d import ENGINE_ID as LEAFLET_ENGINE_ID
from .leaflet_2d import descriptor as leaflet_descriptor
from .maplibre_hybrid import ENGINE_ID as MAPLIBRE_ENGINE_ID
from .maplibre_hybrid import descriptor as maplibre_descriptor


def supported_engines() -> List[Dict[str, Any]]:
    return [leaflet_descriptor(), maplibre_descriptor(), cesium_descriptor()]


def supported_engine_ids() -> set[str]:
    return {LEAFLET_ENGINE_ID, MAPLIBRE_ENGINE_ID, CESIUM_ENGINE_ID}


DEFAULT_ENGINE_ID = LEAFLET_ENGINE_ID
