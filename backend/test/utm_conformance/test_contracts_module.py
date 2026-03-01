import unittest

from utm_agent.contracts import (
    CommandAuditEnvelope,
    OperationalIntentContract,
    SubscriptionContract,
)


class ContractsModuleTests(unittest.TestCase):
    def test_operational_intent_contract_defaults(self) -> None:
        contract = OperationalIntentContract.from_payload(
            {
                "manager_uss_id": "uss-a",
                "volume4d": {
                    "x": [0, 10],
                    "y": [0, 20],
                    "z": [0, 80],
                    "time_start": "2026-02-27T12:00:00Z",
                    "time_end": "2026-02-27T12:20:00Z",
                },
            }
        )
        self.assertTrue(contract.intent_id.startswith("oi-"))
        self.assertEqual(contract.conflict_policy, "reject")
        self.assertEqual(contract.volume4d.as_dict()["x"], [0.0, 10.0])

    def test_subscription_contract_default_notify_for(self) -> None:
        contract = SubscriptionContract.from_payload(
            {
                "manager_uss_id": "uss-watch",
                "volume4d": {
                    "x": [0, 100],
                    "y": [0, 100],
                    "z": [0, 120],
                    "time_start": "2026-02-27T12:00:00Z",
                    "time_end": "2026-02-27T12:30:00Z",
                },
            }
        )
        self.assertEqual(contract.notify_for, ["create", "update", "delete"])

    def test_command_audit_envelope(self) -> None:
        env = CommandAuditEnvelope.from_command(
            {"domain": "utm", "op": "verify_flight_plan", "step_id": "s1", "params": {"uav_id": "uav-1"}},
            {"mission_id": "m1", "task_id": "t1"},
        )
        row = env.as_dict()
        self.assertTrue(str(row.get("command_id", "")).startswith("cmd-"))
        self.assertEqual(str(row.get("correlation_id")), "t1")
        self.assertEqual(str(row.get("mission_id")), "m1")


if __name__ == "__main__":
    unittest.main()
