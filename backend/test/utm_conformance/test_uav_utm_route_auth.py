import os
import unittest

try:
    from fastapi.testclient import TestClient
    from uav_agent.api import app
    from uav_agent.api_shared import UTM_DB_MIRROR
except Exception as exc:  # pragma: no cover - optional deps may be missing in local env
    TestClient = None
    app = None
    UTM_DB_MIRROR = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class UavUtmRouteAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if TestClient is None or app is None or UTM_DB_MIRROR is None:
            raise unittest.SkipTest(f"uav auth test imports unavailable: {_IMPORT_ERROR_MESSAGE}")
        cls._old_security_controls = UTM_DB_MIRROR.get_state("security_controls")
        UTM_DB_MIRROR.set_state(
            "security_controls",
            {
                "service_tokens": {
                    "uav-test-admin": ["admin"],
                    "uav-test-read": ["read"],
                }
            },
        )
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        if UTM_DB_MIRROR is None:
            return
        if isinstance(cls._old_security_controls, dict):
            UTM_DB_MIRROR.set_state("security_controls", cls._old_security_controls)
        else:
            UTM_DB_MIRROR.delete_state("security_controls")

    def setUp(self) -> None:
        os.environ["UAV_ENFORCE_UTM_SERVICE_AUTH"] = "true"

    def test_missing_bearer_rejected(self) -> None:
        resp = self.client.get("/api/utm/weather?airspace_segment=sector-A3")
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body.get("error"), "missing_bearer_token")

    def test_read_token_allows_read_endpoint(self) -> None:
        resp = self.client.get(
            "/api/utm/weather?airspace_segment=sector-A3",
            headers={"Authorization": "Bearer uav-test-read"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "success")

    def test_read_token_rejected_for_mutating_utm_endpoint(self) -> None:
        resp = self.client.post(
            "/api/utm/weather",
            headers={"Authorization": "Bearer uav-test-read"},
            json={
                "airspace_segment": "sector-A3",
                "wind_mps": 7,
                "visibility_km": 10,
                "precip_mmph": 0,
                "storm_alert": False,
            },
        )
        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body.get("error"), "insufficient_role")
        self.assertEqual(body.get("required_role"), "utm_write")

    def test_admin_token_allows_mutating_utm_endpoint(self) -> None:
        resp = self.client.post(
            "/api/utm/weather",
            headers={"Authorization": "Bearer uav-test-admin"},
            json={
                "airspace_segment": "sector-A3",
                "wind_mps": 7,
                "visibility_km": 10,
                "precip_mmph": 0,
                "storm_alert": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "success")


if __name__ == "__main__":
    unittest.main()
