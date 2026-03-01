import os
import sys
import unittest
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from uav_agent.simulator import SIM
    from uav_agent import tools as uav_tools
except Exception as exc:  # pragma: no cover - optional deps may be missing in local env
    SIM = None
    uav_tools = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class UavControlAdapterTests(unittest.TestCase):
    _ENV_KEYS = [
        "UAV_CONTROL_ADAPTER_MODE",
        "UAV_CONTROL_HTTP_BASE_URL",
        "UAV_CONTROL_HTTP_PATH_TEMPLATE",
        "UAV_CONTROL_HTTP_TIMEOUT_S",
        "UAV_CONTROL_HTTP_AUTH_TOKEN",
        "UAV_CONTROL_MIRROR_MODE",
        "UAV_CONTROL_ADAPTER_FALLBACK_TO_SIM",
    ]

    @classmethod
    def setUpClass(cls) -> None:
        if SIM is None or uav_tools is None:
            raise unittest.SkipTest(f"uav control adapter imports unavailable: {_IMPORT_ERROR_MESSAGE}")

    def setUp(self) -> None:
        self._env_backup = {k: os.environ.get(k) for k in self._ENV_KEYS}

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _seed_ready_uav(self) -> str:
        uav_id = f"uav-adapter-{uuid.uuid4().hex[:8]}"
        SIM.plan_route(
            uav_id,
            route_id=f"{uav_id}-route-1",
            waypoints=[
                {"x": 0.0, "y": 0.0, "z": 0.0},
                {"x": 30.0, "y": 10.0, "z": 20.0},
            ],
        )
        SIM.set_approval(
            uav_id,
            {
                "approved": True,
                "signature_verified": True,
                "approval_id": f"approval-{uav_id}",
                "route_id": f"{uav_id}-route-1",
            },
        )
        return uav_id

    def test_default_control_adapter_uses_simulator(self) -> None:
        os.environ["UAV_CONTROL_ADAPTER_MODE"] = "sim"
        uav_id = self._seed_ready_uav()
        out = uav_tools.uav_launch.invoke({"uav_id": uav_id, "require_utm_approval": False})
        self.assertEqual(str(out.get("status", "")), "success")
        adapter = out.get("control_adapter") if isinstance(out.get("control_adapter"), dict) else {}
        self.assertEqual(str(adapter.get("used_mode", "")), "sim")
        self.assertFalse(bool(adapter.get("fallback_used")))

    def test_http_control_adapter_can_fallback_to_simulator(self) -> None:
        os.environ["UAV_CONTROL_ADAPTER_MODE"] = "http"
        os.environ["UAV_CONTROL_HTTP_BASE_URL"] = "http://127.0.0.1:9"
        os.environ["UAV_CONTROL_ADAPTER_FALLBACK_TO_SIM"] = "1"
        os.environ["UAV_CONTROL_MIRROR_MODE"] = "optimistic"
        uav_id = self._seed_ready_uav()
        out = uav_tools.uav_launch.invoke({"uav_id": uav_id, "require_utm_approval": False})
        self.assertEqual(str(out.get("status", "")), "success")
        adapter = out.get("control_adapter") if isinstance(out.get("control_adapter"), dict) else {}
        self.assertEqual(str(adapter.get("resolved_mode", "")), "http")
        self.assertEqual(str(adapter.get("used_mode", "")), "sim")
        self.assertTrue(bool(adapter.get("fallback_used")))
        adapter_result = out.get("adapter_result") if isinstance(out.get("adapter_result"), dict) else {}
        self.assertEqual(str(adapter_result.get("status", "")), "error")

    def test_http_control_adapter_without_fallback_returns_error(self) -> None:
        os.environ["UAV_CONTROL_ADAPTER_MODE"] = "http"
        os.environ["UAV_CONTROL_HTTP_BASE_URL"] = "http://127.0.0.1:9"
        os.environ["UAV_CONTROL_ADAPTER_FALLBACK_TO_SIM"] = "0"
        uav_id = self._seed_ready_uav()
        out = uav_tools.uav_launch.invoke({"uav_id": uav_id, "require_utm_approval": False})
        self.assertEqual(str(out.get("status", "")), "error")
        adapter = out.get("control_adapter") if isinstance(out.get("control_adapter"), dict) else {}
        self.assertEqual(str(adapter.get("resolved_mode", "")), "http")
        self.assertFalse(bool(adapter.get("fallback_used")))
        self.assertFalse(bool(SIM.status(uav_id).get("armed")))


if __name__ == "__main__":
    unittest.main()
