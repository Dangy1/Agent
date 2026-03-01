import unittest
from uuid import uuid4

from agent_db import AgentDB
from mission_supervisor_agent.command_bus import AuditableCommandBus
from mission_supervisor_agent.task_memory import TaskMemoryStore


class _CountingDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, command: dict, state: dict) -> dict:
        self.calls += 1
        return {
            "status": "success",
            "agent": str(command.get("domain") or "unknown"),
            "result": {
                "calls": self.calls,
                "domain": command.get("domain"),
                "op": command.get("op"),
            },
        }


class MissionSupervisorCommandBusMemoryTests(unittest.TestCase):
    def _build_bus(self) -> tuple[AuditableCommandBus, _CountingDispatcher]:
        test_agent = f"mission_supervisor_test_{uuid4().hex[:8]}"
        db = AgentDB(test_agent)
        dispatcher = _CountingDispatcher()
        bus = AuditableCommandBus(dispatcher, db=db, memory=TaskMemoryStore(db=db))
        return bus, dispatcher

    def test_observe_command_replays_from_memory(self) -> None:
        bus, dispatcher = self._build_bus()
        state = {"mission_id": f"mission-{uuid4().hex[:8]}", "task_id": f"task-{uuid4().hex[:8]}"}
        command = {"domain": "network", "op": "health", "params": {}, "step_id": "net-health"}

        first = bus.execute(command, state)
        second = bus.execute(command, state)

        self.assertEqual(dispatcher.calls, 1)
        self.assertEqual(first["audit"]["replayed"], False)
        self.assertEqual(second["audit"]["replayed"], True)
        self.assertEqual(first["result"]["result"]["calls"], 1)
        self.assertEqual(second["result"]["result"]["calls"], 1)
        self.assertIn("protocol_trace", first["audit"])
        self.assertIn("a2a", first["audit"]["protocol_trace"])
        self.assertIn("mcp", first["audit"]["protocol_trace"])

    def test_actuate_command_not_replayed_without_idempotent_flag(self) -> None:
        bus, dispatcher = self._build_bus()
        state = {"mission_id": f"mission-{uuid4().hex[:8]}", "task_id": f"task-{uuid4().hex[:8]}"}
        command = {"domain": "uav", "op": "launch", "params": {"uav_id": "uav-1"}, "step_id": "uav-launch"}

        first = bus.execute(command, state)
        second = bus.execute(command, state)

        self.assertEqual(dispatcher.calls, 2)
        self.assertEqual(first["audit"]["replayed"], False)
        self.assertEqual(second["audit"]["replayed"], False)

    def test_actuate_command_replayed_when_marked_idempotent(self) -> None:
        bus, dispatcher = self._build_bus()
        state = {"mission_id": f"mission-{uuid4().hex[:8]}", "task_id": f"task-{uuid4().hex[:8]}"}
        command = {
            "domain": "uav",
            "op": "launch",
            "params": {"uav_id": "uav-1", "_idempotent": True},
            "step_id": "uav-launch-idempotent",
        }

        first = bus.execute(command, state)
        second = bus.execute(command, state)

        self.assertEqual(dispatcher.calls, 1)
        self.assertEqual(first["audit"]["replayed"], False)
        self.assertEqual(second["audit"]["replayed"], True)


if __name__ == "__main__":
    unittest.main()
