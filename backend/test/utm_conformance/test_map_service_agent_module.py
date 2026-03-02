from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.map_agent.map_agent_api import MapServiceAgent


def test_map_service_agent_toggle_view_and_bounds() -> None:
    agent = MapServiceAgent()

    toggled = agent.toggle_view("2D_Leaflet")
    assert toggled["activeEngine"] == "2D_Leaflet"

    agent.plot_point({"id": "pt-a", "lon": 24.90, "lat": 60.10, "alt": 12})
    agent.plot_point({"id": "pt-b", "lon": 25.10, "lat": 60.30, "alt": 18})
    bounds = agent.current_bounds()

    assert bounds["source"] == "plotted-points"
    assert bounds["pointCount"] == 2
    assert float(bounds["west"]) <= 24.90
    assert float(bounds["east"]) >= 25.10
    assert float(bounds["south"]) <= 60.10
    assert float(bounds["north"]) >= 60.30


def test_map_service_agent_toggle_view_rejects_unknown_engine() -> None:
    agent = MapServiceAgent()
    with pytest.raises(ValueError):
        agent.toggle_view("UNKNOWN_ENGINE")


def test_map_service_agent_sync_broker_roundtrip() -> None:
    agent = MapServiceAgent()
    scope = "pytest-map-sync"

    agent.sync_utm_state(
        {
            "no_fly_zones": [
                {"zone_id": "nfz-1", "lon": 24.93, "lat": 60.17, "radius_m": 80.0, "z_min": 0.0, "z_max": 120.0}
            ]
        },
        scope=scope,
        source="utm",
    )
    agent.sync_uav_fleet(
        {
            "fleet": {
                "uav-1": {
                    "position": {"lon": 24.94, "lat": 60.18, "altM": 55.0},
                    "velocity_mps": 13.0,
                    "waypoints": [
                        {"lon": 24.94, "lat": 60.18, "altM": 55.0},
                        {"lon": 24.95, "lat": 60.19, "altM": 58.0},
                    ],
                }
            }
        },
        scope=scope,
        source="uav",
    )
    agent.sync_network_state(
        {
            "baseStations": [{"id": "BS-1", "lon": 24.92, "lat": 60.16, "status": "online"}],
            "coverage": [{"bsId": "BS-1", "radiusM": 420.0}],
            "trackingSnapshots": [{"id": "uav-1", "lon": 24.94, "lat": 60.18, "altM": 55.0}],
        },
        scope=scope,
        source="network",
    )

    snapshot = agent.sync_state(scope=scope, include_shared=False)
    layers = snapshot["layers"]
    assert len(layers["noFlyZones"]) >= 1
    assert len(layers["uavs"]) >= 1
    assert len(layers["paths"]) >= 1
    assert len(layers["baseStations"]) >= 1
    assert len(layers["coverage"]) >= 1

    events = agent.sync_events(scope=scope, limit=20)
    assert events["count"] >= 3
