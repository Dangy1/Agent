from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict

from agent_db import AgentDB
from utm_agent.contracts import CommandAuditEnvelope


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AuditableCommandBus:
    """Single mission command path with persistent audit envelopes."""

    def __init__(self, dispatcher: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]], *, db: AgentDB | None = None) -> None:
        self._dispatcher = dispatcher
        self._db = db or AgentDB("mission_supervisor")

    def execute(self, command: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        envelope = CommandAuditEnvelope.from_command(command, state).as_dict()
        request = {
            **envelope,
            "params": dict(command.get("params") or {}),
            "requested_at": _utc_now(),
            "status": "dispatched",
        }
        self._db.record_action("mission_command_dispatched", payload=request, entity_id=str(envelope.get("mission_id") or ""))

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
        }
        self._db.record_action("mission_command_completed", payload=request, result=response, entity_id=str(envelope.get("mission_id") or ""))
        return {"audit": response, "result": result}


__all__ = ["AuditableCommandBus"]
