import unittest
from uuid import uuid4

from agent_db import AgentDB
try:
    from mission_supervisor_agent.runtime import MissionRuntimeService

    _RUNTIME_READY = True
    _RUNTIME_SKIP_REASON = ""
except Exception as exc:  # pragma: no cover
    MissionRuntimeService = None  # type: ignore[assignment]
    _RUNTIME_READY = False
    _RUNTIME_SKIP_REASON = f"mission runtime dependencies unavailable: {exc}"


@unittest.skipUnless(_RUNTIME_READY, _RUNTIME_SKIP_REASON)
class MissionSupervisorProtocolTraceTests(unittest.TestCase):
    def _service(self) -> MissionRuntimeService:
        return MissionRuntimeService(db=AgentDB(f"mission_supervisor_trace_test_{uuid4().hex[:8]}"))

    def test_protocol_trace_filters_replayed(self) -> None:
        svc = self._service()
        mission_id = f"mission-{uuid4().hex[:8]}"
        key = svc._state_key(mission_id)  # type: ignore[attr-defined]
        svc.db.set_state(
            key,
            {
                "mission_id": mission_id,
                "graph_state": {
                    "command_bus_log": [
                        {
                            "command_id": "cmd-1",
                            "correlation_id": "task-1",
                            "mission_id": mission_id,
                            "step_id": "s1",
                            "domain": "network",
                            "op": "health",
                            "status": "success",
                            "responded_at": "2026-03-01T00:00:00Z",
                            "replayed": False,
                            "protocol_trace": {"a2a": {"message_id": "a2a-1"}, "mcp": {"tool": "mcp_health"}},
                        },
                        {
                            "command_id": "cmd-2",
                            "correlation_id": "task-1",
                            "mission_id": mission_id,
                            "step_id": "s2",
                            "domain": "network",
                            "op": "health",
                            "status": "success",
                            "responded_at": "2026-03-01T00:00:01Z",
                            "replayed": True,
                            "protocol_trace": {"a2a": {"message_id": "a2a-2"}, "mcp": {"tool": "mcp_health"}},
                        },
                    ]
                },
            },
        )

        all_rows = svc.get_protocol_trace(mission_id, include_replayed=True)
        self.assertEqual(len(all_rows), 2)

        filtered = svc.get_protocol_trace(mission_id, include_replayed=False)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["command_id"], "cmd-1")

    def test_protocol_trace_limit(self) -> None:
        svc = self._service()
        mission_id = f"mission-{uuid4().hex[:8]}"
        key = svc._state_key(mission_id)  # type: ignore[attr-defined]
        rows = []
        for i in range(5):
            rows.append(
                {
                    "command_id": f"cmd-{i}",
                    "correlation_id": "task-1",
                    "mission_id": mission_id,
                    "step_id": f"s{i}",
                    "domain": "dss",
                    "op": "query_operational_intents",
                    "status": "success",
                    "responded_at": f"2026-03-01T00:00:0{i}Z",
                    "replayed": False,
                    "protocol_trace": {"a2a": {"message_id": f"a2a-{i}"}, "mcp": {"tool": "dss_query_operational_intents"}},
                }
            )
        svc.db.set_state(key, {"mission_id": mission_id, "graph_state": {"command_bus_log": rows}})

        out = svc.get_protocol_trace(mission_id, limit=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["command_id"], "cmd-3")
        self.assertEqual(out[1]["command_id"], "cmd-4")

    def test_protocol_trace_mermaid_contains_message_flow(self) -> None:
        svc = self._service()
        mission_id = f"mission-{uuid4().hex[:8]}"
        key = svc._state_key(mission_id)  # type: ignore[attr-defined]
        svc.db.set_state(
            key,
            {
                "mission_id": mission_id,
                "graph_state": {
                    "command_bus_log": [
                        {
                            "command_id": "cmd-1",
                            "correlation_id": "task-1",
                            "mission_id": mission_id,
                            "step_id": "s1",
                            "domain": "network",
                            "op": "health",
                            "status": "success",
                            "responded_at": "2026-03-01T00:00:00Z",
                            "replayed": False,
                            "protocol_trace": {
                                "a2a": {
                                    "params": {
                                        "message": {
                                            "metadata": {
                                                "sender": "mission_supervisor",
                                                "receiver": "network",
                                            }
                                        }
                                    }
                                },
                                "mcp": {"tool": "mcp_health"},
                            },
                        },
                        {
                            "command_id": "cmd-2",
                            "correlation_id": "task-1",
                            "mission_id": mission_id,
                            "step_id": "s2",
                            "domain": "network",
                            "op": "health",
                            "status": "success",
                            "responded_at": "2026-03-01T00:00:01Z",
                            "replayed": True,
                            "protocol_trace": {
                                "a2a": {"sender": "mission_supervisor", "receiver": "network"},
                                "mcp": {"tool": "mcp_health"},
                            },
                        },
                    ]
                },
            },
        )

        mermaid = svc.get_protocol_trace_mermaid(mission_id, include_replayed=True)
        self.assertIn("sequenceDiagram", mermaid)
        self.assertIn("mission_supervisor", mermaid)
        self.assertIn("network", mermaid)
        self.assertIn("network.health [success, live]", mermaid)
        self.assertIn("network.health [success, replayed]", mermaid)

        filtered = svc.get_protocol_trace_mermaid(mission_id, include_replayed=False)
        self.assertIn("network.health [success, live]", filtered)
        self.assertNotIn("network.health [success, replayed]", filtered)


if __name__ == "__main__":
    unittest.main()
