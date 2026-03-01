import unittest

try:
    from utm_agent.api import OperationalIntentPayload, Volume4DPayload, delete_operational_intent, get_query_operational_intents, put_operational_intent
except Exception as exc:  # pragma: no cover - optional deps may be missing in local env
    OperationalIntentPayload = None
    Volume4DPayload = None
    put_operational_intent = None
    get_query_operational_intents = None
    delete_operational_intent = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class PhaseAApiEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if put_operational_intent is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_put_operational_intent_endpoint_upserts_by_path_id(self) -> None:
        payload = OperationalIntentPayload(  # type: ignore[misc]
            manager_uss_id="uss-put-test",
            state="accepted",
            priority="normal",
            conflict_policy="reject",
            volume4d=Volume4DPayload(  # type: ignore[misc]
                x=[1, 2],
                y=[3, 4],
                z=[0, 50],
                time_start="2026-02-27T12:00:00Z",
                time_end="2026-02-27T12:20:00Z",
            ),
            constraints={"max_speed_mps": 18},
            metadata={"source": "test_put"},
        )
        response = put_operational_intent("phase-a-put-1", payload)  # type: ignore[misc]
        self.assertEqual(response.get("status"), "success")

        query = get_query_operational_intents(manager_uss_id="uss-put-test", states="accepted")  # type: ignore[misc]
        self.assertEqual(query.get("status"), "success")
        items = ((query.get("result") or {}).get("items") if isinstance(query.get("result"), dict) else [])
        match = next((x for x in items if isinstance(x, dict) and x.get("intent_id") == "phase-a-put-1"), None)
        self.assertIsNotNone(match)
        self.assertEqual((match or {}).get("constraints"), {"max_speed_mps": 18})

        cleanup = delete_operational_intent("phase-a-put-1")  # type: ignore[misc]
        self.assertEqual(cleanup.get("status"), "success")


if __name__ == "__main__":
    unittest.main()
