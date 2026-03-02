from __future__ import annotations

from typing import Any, Dict


ENGINE_ID = "2.5D_MapLibre"


def descriptor() -> Dict[str, Any]:
    return {
        "id": ENGINE_ID,
        "label": "MapLibre Hybrid",
        "dimension": "2.5D",
        "capabilities": ["terrain-rgb", "vector-overlays", "pitch", "bearing"],
    }
