import unittest

from mission_supervisor_agent.a2a_protocol import (
    A2AEnvelope,
    command_to_mcp_invocation,
    command_to_mcp_tool,
)


class A2AProtocolTests(unittest.TestCase):
    def test_observe_envelope_and_mcp_mapping(self) -> None:
        command = {"domain": "network", "op": "health", "params": {}, "step_id": "net-health"}
        state = {"mission_id": "mission-a2a-1", "task_id": "task-a2a-1", "mission_phase": "preflight"}

        env = A2AEnvelope.from_command(command, state).as_dict()
        self.assertEqual(env["protocol"], "google.a2a+json")
        self.assertEqual(env["receiver"], "network")
        self.assertEqual(env["intent_type"], "observe")
        self.assertTrue(bool(env["constraints"]["deterministic_hint"]))
        self.assertEqual(env["command"]["step_id"], "net-health")

        mcp = command_to_mcp_invocation(command, state)
        self.assertEqual(mcp["tool"], "mcp_health")
        self.assertEqual(mcp["correlation_id"], "task-a2a-1")
        self.assertEqual(mcp["mission_id"], "mission-a2a-1")

    def test_actuate_envelope_deterministic_hint_only_when_idempotent(self) -> None:
        command = {"domain": "uav", "op": "launch", "params": {"uav_id": "uav-1"}}
        state = {"mission_id": "mission-a2a-2", "task_id": "task-a2a-2", "mission_phase": "launch"}

        env = A2AEnvelope.from_command(command, state).as_dict()
        self.assertEqual(env["intent_type"], "actuate")
        self.assertFalse(bool(env["constraints"]["deterministic_hint"]))

        idempotent_cmd = {"domain": "uav", "op": "launch", "params": {"uav_id": "uav-1", "_idempotent": True}}
        idempotent_env = A2AEnvelope.from_command(idempotent_cmd, state).as_dict()
        self.assertTrue(bool(idempotent_env["constraints"]["deterministic_hint"]))

    def test_unknown_mapping_falls_back_to_domain_op(self) -> None:
        command = {"domain": "network", "op": "custom_op", "params": {}}
        self.assertEqual(command_to_mcp_tool(command), "network.custom_op")


if __name__ == "__main__":
    unittest.main()
