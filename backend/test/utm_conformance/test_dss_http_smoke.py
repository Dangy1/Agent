import json
import os
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = os.getenv("UTM_BASE_URL", "http://127.0.0.1:8021").rstrip("/")
SERVICE_TOKEN = os.getenv("UTM_SERVICE_TOKEN", "local-dev-token")


def _http(method: str, path: str, *, payload: dict | None = None, params: dict | None = None, timeout_s: float = 6.0) -> dict:
    query = f"?{urlencode(params)}" if isinstance(params, dict) and params else ""
    url = f"{BASE_URL}{path}{query}"
    data = json.dumps(payload).encode("utf-8") if isinstance(payload, dict) else None
    headers = {"Accept": "application/json"}
    if SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {SERVICE_TOKEN}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url=url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {"status": "success"}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"HTTP {exc.code} {method} {path}: {detail}") from exc
    except URLError as exc:
        raise AssertionError(f"Connection failure {method} {path}: {exc}") from exc


def _volume(x0: float, x1: float, y0: float, y1: float, z1: float = 100.0) -> dict:
    return {
        "x": [x0, x1],
        "y": [y0, y1],
        "z": [0.0, z1],
        "time_start": "2026-02-27T12:00:00Z",
        "time_end": "2026-02-27T12:20:00Z",
    }


class DssHttpSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            res = _http("GET", "/api/utm/sync", timeout_s=2.0)
            if str(res.get("status")) != "success":
                raise AssertionError(f"Unexpected sync response: {res}")
        except Exception as exc:
            raise unittest.SkipTest(f"UTM API not reachable at {BASE_URL}: {exc}")

    def _uid(self, prefix: str) -> str:
        return f"{prefix}-{int(time.time() * 1000)}"

    def test_phase_a_operational_intents_and_subscriptions(self) -> None:
        manager = self._uid("uss-local-smoke")
        intent_id = self._uid("smoke-oi")
        sub_id = self._uid("smoke-sub")
        base = (int(time.time() * 1000) % 100000) + 50000

        try:
            sub_create = _http(
                "POST",
                "/api/utm/dss/subscriptions",
                payload={
                    "subscription_id": sub_id,
                    "manager_uss_id": manager,
                    "callback_url": "local://uss-watch/callback",
                    "volume4d": _volume(base - 20, base + 120, base - 20, base + 120, 120),
                    "notify_for": ["create", "update", "delete"],
                },
            )
            self.assertEqual(sub_create.get("status"), "success")

            create = _http(
                "POST",
                "/api/utm/dss/operational-intents",
                payload={
                    "intent_id": intent_id,
                    "manager_uss_id": manager,
                    "state": "accepted",
                    "priority": "normal",
                    "conflict_policy": "reject",
                    "volume4d": _volume(base, base + 10, base, base + 10),
                    "constraints": {"max_speed_mps": 20},
                },
            )
            self.assertEqual(create.get("status"), "success")
            self.assertTrue(bool((((create.get("result") or {}).get("upsert") or {}).get("stored"))))

            update = _http(
                "PUT",
                f"/api/utm/dss/operational-intents/{intent_id}",
                payload={
                    "manager_uss_id": manager,
                    "state": "activated",
                    "priority": "high",
                    "conflict_policy": "reject",
                    "volume4d": _volume(base + 1, base + 11, base + 1, base + 11),
                    "constraints": {"max_speed_mps": 18},
                },
            )
            self.assertEqual(update.get("status"), "success")
            item = (((update.get("result") or {}).get("upsert") or {}).get("intent") or {})
            self.assertEqual(str(item.get("intent_id")), intent_id)
            self.assertEqual((item.get("constraints") or {}).get("max_speed_mps"), 18)

            query = _http(
                "GET",
                "/api/utm/dss/operational-intents/query",
                params={"manager_uss_id": manager, "states": "activated"},
            )
            items = ((query.get("result") or {}).get("items") or [])
            self.assertTrue(any(isinstance(x, dict) and x.get("intent_id") == intent_id for x in items))

            subs = _http("GET", "/api/utm/dss/subscriptions", params={"manager_uss_id": manager})
            sub_items = ((subs.get("result") or {}).get("items") or [])
            self.assertTrue(any(isinstance(x, dict) and x.get("subscription_id") == sub_id for x in sub_items))
        finally:
            _http("DELETE", f"/api/utm/dss/operational-intents/{intent_id}")
            _http("DELETE", f"/api/utm/dss/subscriptions/{sub_id}")

    def test_phase_a_strategic_conflict_reject(self) -> None:
        manager_a = self._uid("uss-local-a")
        manager_b = self._uid("uss-local-b")
        intent_a = self._uid("conf-a")
        intent_b = self._uid("conf-b")
        base = (int(time.time() * 1000) % 100000) + 200000
        try:
            a = _http(
                "POST",
                "/api/utm/dss/operational-intents",
                payload={
                    "intent_id": intent_a,
                    "manager_uss_id": manager_a,
                    "state": "accepted",
                    "priority": "normal",
                    "conflict_policy": "reject",
                    "volume4d": _volume(base, base + 30, base, base + 30),
                },
            )
            self.assertEqual(a.get("status"), "success")
            self.assertTrue(bool((((a.get("result") or {}).get("upsert") or {}).get("stored"))))

            b = _http(
                "POST",
                "/api/utm/dss/operational-intents",
                payload={
                    "intent_id": intent_b,
                    "manager_uss_id": manager_b,
                    "state": "accepted",
                    "priority": "normal",
                    "conflict_policy": "reject",
                    "volume4d": _volume(base + 10, base + 40, base + 10, base + 40),
                },
            )
            self.assertEqual(b.get("status"), "success")
            b_upsert = ((b.get("result") or {}).get("upsert") or {})
            self.assertEqual(b_upsert.get("status"), "rejected")
            self.assertFalse(bool(b_upsert.get("stored")))
        finally:
            _http("DELETE", f"/api/utm/dss/operational-intents/{intent_a}")
            _http("DELETE", f"/api/utm/dss/operational-intents/{intent_b}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
