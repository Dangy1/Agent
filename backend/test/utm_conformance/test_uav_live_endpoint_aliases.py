import unittest
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from uav_agent.api import app
except Exception as exc:  # pragma: no cover
    app = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class UavLiveEndpointAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if app is None:
            raise unittest.SkipTest(f"uav live alias imports unavailable: {_IMPORT_ERROR_MESSAGE}")

    def _route_for(self, *, path: str, method: str):
        method_u = method.upper()
        for route in app.routes:
            route_path = getattr(route, "path", None)
            route_methods = getattr(route, "methods", None)
            if route_path == path and isinstance(route_methods, set) and method_u in route_methods:
                return route
        return None

    def test_live_routes_exist_for_core_mission_workflow(self) -> None:
        required = [
            ("/api/uav/live/state", "GET"),
            ("/api/uav/live/control-adapter", "GET"),
            ("/api/uav/control/_contract", "GET"),
            ("/api/uav/control/{operation}", "POST"),
            ("/api/uav/live/plan", "POST"),
            ("/api/uav/live/geofence-submit", "POST"),
            ("/api/uav/live/request-approval", "POST"),
            ("/api/uav/live/utm-submit-mission", "POST"),
            ("/api/uav/live/replan-via-utm-nfz", "POST"),
            ("/api/uav/live/launch", "POST"),
            ("/api/uav/live/step", "POST"),
            ("/api/uav/live/hold", "POST"),
            ("/api/uav/live/resume", "POST"),
            ("/api/uav/live/rth", "POST"),
            ("/api/uav/live/land", "POST"),
            ("/api/uav/live/end-mission", "POST"),
        ]
        for path, method in required:
            with self.subTest(path=path, method=method):
                self.assertIsNotNone(self._route_for(path=path, method=method))

    def test_sim_and_live_paths_share_same_endpoint_function(self) -> None:
        pairs = [
            ("/api/uav/sim/state", "GET", "/api/uav/live/state", "GET"),
            ("/api/uav/sim/utm-submit-mission", "POST", "/api/uav/live/utm-submit-mission", "POST"),
            ("/api/uav/sim/launch", "POST", "/api/uav/live/launch", "POST"),
        ]
        for sim_path, sim_method, live_path, live_method in pairs:
            with self.subTest(sim_path=sim_path, live_path=live_path):
                sim_route = self._route_for(path=sim_path, method=sim_method)
                live_route = self._route_for(path=live_path, method=live_method)
                self.assertIsNotNone(sim_route)
                self.assertIsNotNone(live_route)
                self.assertIs(getattr(sim_route, "endpoint", None), getattr(live_route, "endpoint", None))


if __name__ == "__main__":
    unittest.main()
