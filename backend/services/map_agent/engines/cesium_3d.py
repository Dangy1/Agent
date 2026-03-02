from __future__ import annotations

from typing import Any, Dict


ENGINE_ID = "3D_Cesium"


def descriptor() -> Dict[str, Any]:
    return {
        "id": ENGINE_ID,
        "label": "Cesium 3D",
        "dimension": "3D",
        "capabilities": ["globe", "terrain", "atmosphere", "3d-overlays"],
    }
