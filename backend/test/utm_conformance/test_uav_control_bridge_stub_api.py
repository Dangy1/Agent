import sys
import unittest
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    import uav_agent.api_routes_uav as api_routes_uav
    from uav_agent.api_models import UavControlBridgeCommandPayload
    from uav_agent.api_shared import UAV_DB
except Exception as exc:  # pragma: no cover
    api_routes_uav = None
    UavControlBridgeCommandPayload = None
    UAV_DB = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class UavControlBridgeStubApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if api_routes_uav is None or UavControlBridgeCommandPayload is None or UAV_DB is None:
            raise unittest.SkipTest(f"uav control bridge imports unavailable: {_IMPORT_ERROR_MESSAGE}")

    def setUp(self) -> None:
        self._old_stub_state = UAV_DB.get_state("uav_control_bridge_stub_state")
        UAV_DB.set_state("uav_control_bridge_stub_state", {})

    def tearDown(self) -> None:
        if isinstance(self._old_stub_state, dict):
            UAV_DB.set_state("uav_control_bridge_stub_state", self._old_stub_state)
        else:
            UAV_DB.delete_state("uav_control_bridge_stub_state")

    def test_contract_endpoint_exposes_request_and_response_schema(self) -> None:
        body = api_routes_uav.get_uav_control_contract()
        self.assertEqual(str(body.get("status", "")), "success")
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        self.assertEqual(str(result.get("path", "")), "/api/uav/control/{operation}")
        self.assertIn("request_schema", result)
        self.assertIn("response_schema", result)
        self.assertIn("examples", result)

    def test_stub_launch_step_hold_sequence(self) -> None:
        uav_id = f"uav-bridge-{uuid.uuid4().hex[:8]}"
        launch = api_routes_uav.post_uav_control_bridge(
            "launch",
            UavControlBridgeCommandPayload(uav_id=uav_id, operation="launch", params={}),
        )
        self.assertEqual(str(launch.get("status", "")), "success")
        launch_tlm = launch.get("telemetry") if isinstance(launch.get("telemetry"), dict) else {}
        self.assertTrue(bool(launch_tlm.get("armed")))
        self.assertTrue(bool(launch_tlm.get("active")))

        step = api_routes_uav.post_uav_control_bridge(
            "step",
            UavControlBridgeCommandPayload(uav_id=uav_id, operation="step", params={"ticks": 2}),
        )
        self.assertEqual(str(step.get("status", "")), "success")
        step_tlm = step.get("telemetry") if isinstance(step.get("telemetry"), dict) else {}
        self.assertGreaterEqual(int(step_tlm.get("waypoint_index", 0) or 0), 2)
        self.assertLess(float(step_tlm.get("battery_pct", 100.0) or 100.0), 100.0)

        hold = api_routes_uav.post_uav_control_bridge(
            "hold",
            UavControlBridgeCommandPayload(uav_id=uav_id, operation="hold", params={"reason": "operator_request"}),
        )
        hold_tlm = hold.get("telemetry") if isinstance(hold.get("telemetry"), dict) else {}
        self.assertEqual(str(hold_tlm.get("flight_phase", "")), "HOLD")
        self.assertFalse(bool(hold_tlm.get("active")))


if __name__ == "__main__":
    unittest.main()
