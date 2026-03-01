from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from agent_db import AgentDB

from .command_types import classify_command_operation_type


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _to_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def command_fingerprint(command: Dict[str, Any]) -> str:
    normalized = {
        "domain": _clean_str(command.get("domain")),
        "op": _clean_str(command.get("op")),
        "params": _to_dict(command.get("params")),
    }
    raw = json.dumps(normalized, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_idempotent_command(command: Dict[str, Any]) -> bool:
    params = _to_dict(command.get("params"))
    if bool(params.get("_disable_replay")):
        return False
    if bool(params.get("_idempotent")):
        return True
    return classify_command_operation_type(command) == "observe"


class TaskMemoryStore:
    """Mission-scoped working memory for deterministic replay and task facts."""

    def __init__(self, *, db: AgentDB | None = None) -> None:
        self._db = db or AgentDB("mission_supervisor")

    def _key(self, mission_id: str) -> str:
        return f"mission:{mission_id}:task_memory"

    def _load(self, mission_id: str) -> Dict[str, Any]:
        raw = self._db.get_state(self._key(mission_id))
        if not isinstance(raw, dict):
            return {"facts": {}, "command_results": {}, "updated_at": _utc_now()}
        out = dict(raw)
        out.setdefault("facts", {})
        out.setdefault("command_results", {})
        out.setdefault("updated_at", _utc_now())
        return out

    def _save(self, mission_id: str, memory: Dict[str, Any]) -> None:
        mem = dict(memory)
        mem["updated_at"] = _utc_now()
        self._db.set_state(self._key(mission_id), mem)

    def should_replay(self, command: Dict[str, Any]) -> bool:
        return _is_idempotent_command(command)

    def recall_result(self, mission_id: str, command: Dict[str, Any]) -> Dict[str, Any] | None:
        if not self.should_replay(command):
            return None
        memory = self._load(mission_id)
        results = _to_dict(memory.get("command_results"))
        item = results.get(command_fingerprint(command))
        return dict(item) if isinstance(item, dict) else None

    def remember_result(
        self,
        mission_id: str,
        command: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        if not self.should_replay(command):
            return
        memory = self._load(mission_id)
        results = _to_dict(memory.get("command_results"))
        fp = command_fingerprint(command)
        results[fp] = {
            "fingerprint": fp,
            "stored_at": _utc_now(),
            "status": str(result.get("status") or "unknown"),
            "result": dict(result),
        }
        if len(results) > 300:
            rows = sorted(
                [v for v in results.values() if isinstance(v, dict)],
                key=lambda x: str(x.get("stored_at") or ""),
            )
            results = {str(v.get("fingerprint") or ""): v for v in rows[-300:] if str(v.get("fingerprint") or "")}
        memory["command_results"] = results
        self._save(mission_id, memory)

    def set_fact(self, mission_id: str, namespace: str, key: str, value: Any) -> None:
        memory = self._load(mission_id)
        facts = _to_dict(memory.get("facts"))
        ns = _to_dict(facts.get(namespace))
        ns[_clean_str(key)] = value
        facts[_clean_str(namespace, default="default")] = ns
        memory["facts"] = facts
        self._save(mission_id, memory)

    def get_fact(self, mission_id: str, namespace: str, key: str) -> Tuple[bool, Any]:
        memory = self._load(mission_id)
        facts = _to_dict(memory.get("facts"))
        ns = _to_dict(facts.get(namespace))
        if key not in ns:
            return False, None
        return True, ns[key]

    def snapshot(self, mission_id: str) -> Dict[str, Any]:
        return self._load(mission_id)


__all__ = ["TaskMemoryStore", "command_fingerprint"]
