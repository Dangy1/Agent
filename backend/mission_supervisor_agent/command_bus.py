from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict

from agent_db import AgentDB
from utm_agent.contracts import CommandAuditEnvelope

from .a2a_protocol import A2AEnvelope, command_to_mcp_invocation
from .task_memory import TaskMemoryStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AuditableCommandBus:
    """Single mission command path with persistent audit envelopes."""

    def __init__(
        self,
        dispatcher: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        *,
        db: AgentDB | None = None,
        memory: TaskMemoryStore | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._db = db or AgentDB("mission_supervisor")
        self._memory = memory or TaskMemoryStore(db=self._db)

    def _mission_id(self, state: Dict[str, Any]) -> str:
        mission_id = str(state.get("mission_id") or "").strip()
        if mission_id:
            return mission_id
        return str(state.get("task_id") or "mission-local")

    def execute(self, command: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        envelope = CommandAuditEnvelope.from_command(command, state).as_dict()
        a2a = A2AEnvelope.from_command(command, state, receiver=str(command.get("domain") or "unknown")).as_dict()
        mcp = command_to_mcp_invocation(command, state)
        mission_id = self._mission_id(state)
        request = {
            **envelope,
            "params": dict(command.get("params") or {}),
            "requested_at": _utc_now(),
            "status": "dispatched",
            "protocol_trace": {"a2a": a2a, "mcp": mcp},
        }
        self._db.record_action("mission_command_dispatched", payload=request, entity_id=str(envelope.get("mission_id") or ""))
        self._db.record_action(
            "mission_a2a_dispatched",
            payload={"envelope": a2a, "mcp": mcp},
            entity_id=mission_id,
        )

        cached = self._memory.recall_result(mission_id, command)
        if isinstance(cached, dict) and isinstance(cached.get("result"), dict):
            replayed_result = dict(cached.get("result") or {})
            response = {
                **envelope,
                "responded_at": _utc_now(),
                "status": str(replayed_result.get("status", "success")),
                "result_summary": {
                    "agent": replayed_result.get("agent"),
                    "error": replayed_result.get("error"),
                },
                "protocol_trace": {"a2a": a2a, "mcp": mcp},
                "replayed": True,
            }
            self._db.record_action(
                "mission_command_replayed",
                payload=request,
                result=response,
                entity_id=mission_id,
            )
            return {"audit": response, "result": replayed_result}

        result: Dict[str, Any]
        try:
            result = self._dispatcher(command, state)
        except Exception as exc:
            result = {"status": "error", "agent": "command_bus", "error": "dispatch_exception", "details": str(exc)}

        response = {
            **envelope,
            "responded_at": _utc_now(),
            "status": str(result.get("status", "error")),
            "result_summary": {
                "agent": result.get("agent"),
                "error": result.get("error"),
            },
            "protocol_trace": {"a2a": a2a, "mcp": mcp},
            "replayed": False,
        }
        self._db.record_action("mission_command_completed", payload=request, result=response, entity_id=str(envelope.get("mission_id") or ""))
        if isinstance(result, dict) and str(result.get("status", "")).lower() == "success":
            self._memory.remember_result(mission_id, command, result)
        return {"audit": response, "result": result}


__all__ = ["AuditableCommandBus"]
