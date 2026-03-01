import unittest

from utm_agent.dss_adapter import InMemoryDSSAdapter
from utm_agent.operational_intents import upsert_intent
from utm_agent.service import StrategicConflictStatus, evaluate_4d_conflict_status


class PhaseACoreTests(unittest.TestCase):
    def test_service_conflict_status_helper(self) -> None:
        blocking = evaluate_4d_conflict_status(
            candidate_volume4d={"x": [0, 10], "y": [0, 10], "z": [0, 80], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
            other_volume4d={"x": [5, 15], "y": [5, 15], "z": [0, 80], "time_start": "2026-02-27T12:05:00Z", "time_end": "2026-02-27T12:25:00Z"},
            candidate_priority="low",
            other_priority="high",
        )
        self.assertEqual(blocking.status, StrategicConflictStatus.BLOCKING)

        advisory = evaluate_4d_conflict_status(
            candidate_volume4d={"x": [0, 10], "y": [0, 10], "z": [0, 80], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
            other_volume4d={"x": [5, 15], "y": [5, 15], "z": [0, 80], "time_start": "2026-02-27T12:05:00Z", "time_end": "2026-02-27T12:25:00Z"},
            candidate_priority="high",
            other_priority="low",
        )
        self.assertEqual(advisory.status, StrategicConflictStatus.ADVISORY)

    def test_operational_intent_contains_phase_a_fields(self) -> None:
        intents: dict[str, dict] = {}
        out = upsert_intent(
            intents,
            intent_id="oi-a",
            manager_uss_id="uss-a",
            state="accepted",
            priority="normal",
            volume4d={"x": [0, 10], "y": [0, 10], "z": [0, 80], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
            constraints={"max_speed_mps": 20},
            metadata={"source": "test"},
        )
        self.assertEqual(out.get("status"), "success")
        self.assertTrue(bool(out.get("stored")))
        intent = out.get("intent") if isinstance(out.get("intent"), dict) else {}
        for key in (
            "intent_id",
            "manager_uss_id",
            "state",
            "priority",
            "volume4d",
            "ovn",
            "version",
            "time_start",
            "time_end",
            "constraints",
            "updated_at",
        ):
            self.assertIn(key, intent)

    def test_strategic_conflict_reject_blocks_storage(self) -> None:
        intents: dict[str, dict] = {}
        first = upsert_intent(
            intents,
            intent_id="oi-1",
            manager_uss_id="uss-a",
            state="accepted",
            priority="normal",
            conflict_policy="reject",
            volume4d={"x": [0, 30], "y": [0, 30], "z": [0, 100], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:30:00Z"},
        )
        self.assertTrue(bool(first.get("stored")))
        second = upsert_intent(
            intents,
            intent_id="oi-2",
            manager_uss_id="uss-b",
            state="accepted",
            priority="normal",
            conflict_policy="reject",
            volume4d={"x": [10, 40], "y": [10, 40], "z": [0, 100], "time_start": "2026-02-27T12:10:00Z", "time_end": "2026-02-27T12:35:00Z"},
        )
        self.assertEqual(second.get("status"), "rejected")
        self.assertFalse(bool(second.get("stored")))
        self.assertNotIn("oi-2", intents)

    def test_inmemory_adapter_operational_intent_flow(self) -> None:
        adapter = InMemoryDSSAdapter()
        upsert = adapter.upsert_operational_intent(
            {
                "intent_id": "oi-adapter-1",
                "manager_uss_id": "uss-local",
                "state": "accepted",
                "priority": "normal",
                "conflict_policy": "reject",
                "volume4d": {"x": [0, 20], "y": [0, 20], "z": [0, 80], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
            }
        )
        self.assertEqual(upsert.get("status"), "success")
        query = adapter.query_operational_intents({"manager_uss_id": "uss-local"})
        self.assertEqual(query.get("status"), "success")
        result = query.get("result") if isinstance(query.get("result"), dict) else {}
        self.assertEqual(int(result.get("count", 0) or 0), 1)
        delete = adapter.delete_operational_intent("oi-adapter-1")
        self.assertEqual(delete.get("status"), "success")
        self.assertTrue(bool((delete.get("result") or {}).get("deleted")))

    def test_inmemory_adapter_subscription_flow(self) -> None:
        adapter = InMemoryDSSAdapter()
        upsert = adapter.upsert_subscription(
            {
                "subscription_id": "sub-1",
                "manager_uss_id": "uss-watch",
                "callback_url": "local://watch/callback",
                "volume4d": {"x": [0, 100], "y": [0, 100], "z": [0, 120], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T13:00:00Z"},
                "notify_for": ["create", "update", "delete"],
            }
        )
        self.assertEqual(upsert.get("status"), "success")
        query = adapter.query_subscriptions({"manager_uss_id": "uss-watch"})
        self.assertEqual(query.get("status"), "success")
        result = query.get("result") if isinstance(query.get("result"), dict) else {}
        self.assertEqual(int(result.get("count", 0) or 0), 1)
        delete = adapter.delete_subscription("sub-1")
        self.assertEqual(delete.get("status"), "success")
        self.assertTrue(bool((delete.get("result") or {}).get("deleted")))


if __name__ == "__main__":
    unittest.main()
