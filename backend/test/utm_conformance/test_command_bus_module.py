import unittest

from agent_db import AgentDB
from mission_supervisor_agent.command_bus import AuditableCommandBus


class CommandBusModuleTests(unittest.TestCase):
    def test_command_bus_records_audit_envelope(self) -> None:
        def _dispatcher(command: dict, state: dict) -> dict:
            return {"status": "success", "agent": "test", "result": {"echo": command.get("op")}}

        bus = AuditableCommandBus(_dispatcher)
        out = bus.execute(
            {"domain": "utm", "op": "verify_flight_plan", "step_id": "step-1", "params": {"uav_id": "uav-1"}},
            {"mission_id": "mission-bus-1", "task_id": "task-bus-1"},
        )
        audit = out.get("audit") if isinstance(out.get("audit"), dict) else {}
        self.assertTrue(str(audit.get("command_id", "")).startswith("cmd-"))
        self.assertEqual(str(audit.get("correlation_id", "")), "task-bus-1")
        self.assertEqual(str(audit.get("mission_id", "")), "mission-bus-1")
        self.assertEqual(str((out.get("result") or {}).get("status", "")), "success")

        db = AgentDB("mission_supervisor")
        recent = db.recent_actions(20)
        matching = [r for r in recent if str(r.get("action")) in {"mission_command_dispatched", "mission_command_completed"}]
        self.assertGreaterEqual(len(matching), 1)


if __name__ == "__main__":
    unittest.main()
