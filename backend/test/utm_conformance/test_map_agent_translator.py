from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.map_agent.core.translator import gps_to_geojson_feature


def test_gps_to_geojson_feature_has_tm35fin_and_ecef() -> None:
    feature = gps_to_geojson_feature({"id": "tower-a", "lon": 24.9384, "lat": 60.1699, "alt": 32.5})
    assert feature["type"] == "Feature"

    geometry = feature["geometry"]
    assert geometry["type"] == "Point"
    coords = geometry["coordinates"]
    assert abs(float(coords[0]) - 24.9384) < 1e-8
    assert abs(float(coords[1]) - 60.1699) < 1e-8
    assert abs(float(coords[2]) - 32.5) < 1e-6

    props = feature["properties"]
    assert props["id"] == "tower-a"
    assert props["inFinlandBounds"] is True

    tm35 = props["tm35fin"]
    assert 200_000 <= float(tm35["easting"]) <= 800_000
    assert 6_500_000 <= float(tm35["northing"]) <= 7_900_000

    ecef = props["ecef"]
    assert abs(float(ecef["x"])) > 1000
    assert abs(float(ecef["y"])) > 1000
    assert abs(float(ecef["z"])) > 1000


def test_gps_to_geojson_feature_rejects_invalid_lon_lat() -> None:
    with pytest.raises(ValueError):
        gps_to_geojson_feature({"id": "bad", "lon": 195.0, "lat": 95.0, "alt": 0})
