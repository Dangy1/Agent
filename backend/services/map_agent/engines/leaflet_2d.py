from __future__ import annotations

from typing import Any, Dict


ENGINE_ID = "2D_Leaflet"


def descriptor() -> Dict[str, Any]:
    return {
        "id": ENGINE_ID,
        "label": "Leaflet 2D",
        "dimension": "2D",
        "capabilities": ["raster-tiles", "vector-overlays", "pan", "wheel-zoom"],
    }
