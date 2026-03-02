from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agent_db import AgentDB

from .graph import agent as mission_graph


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clone_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_text(value: Any, default: str = "") -> str:
    out = str(value or "").strip()
    return out if out else default


def _trace_sender(row: Dict[str, Any]) -> str:
    trace = row.get("protocol_trace")
    if isinstance(trace, dict):
        a2a = trace.get("a2a")
        if isinstance(a2a, dict):
            params = a2a.get("params")
            if isinstance(params, dict):
                msg = params.get("message")
                if isinstance(msg, dict):
                    metadata = msg.get("metadata")
                    if isinstance(metadata, dict) and _safe_text(metadata.get("sender")):
                        return _safe_text(metadata.get("sender"))
            if _safe_text(a2a.get("sender")):
                return _safe_text(a2a.get("sender"))
    return "mission_supervisor"


def _trace_receiver(row: Dict[str, Any]) -> str:
    trace = row.get("protocol_trace")
    if isinstance(trace, dict):
        a2a = trace.get("a2a")
        if isinstance(a2a, dict):
            params = a2a.get("params")
            if isinstance(params, dict):
                msg = params.get("message")
                if isinstance(msg, dict):
                    metadata = msg.get("metadata")
                    if isinstance(metadata, dict) and _safe_text(metadata.get("receiver")):
                        return _safe_text(metadata.get("receiver"))
            if _safe_text(a2a.get("receiver")):
                return _safe_text(a2a.get("receiver"))
    fallback = _safe_text(row.get("domain"), default="unknown")
    return fallback


def _mermaid_alias(index: int) -> str:
    return f"p{index}"


def _mermaid_label(row: Dict[str, Any]) -> str:
    domain = _safe_text(row.get("domain"), default="unknown")
    op = _safe_text(row.get("op"), default="unknown")
    status = _safe_text(row.get("status"), default="unknown")
    replayed = bool(row.get("replayed"))
    mode = "replayed" if replayed else "live"
    return f"{domain}.{op} [{status}, {mode}]"


class MissionRuntimeService:
    """Runs mission supervisor workflows and persists mission snapshots/events."""

    def __init__(self, *, db: AgentDB | None = None) -> None:
        self.db = db or AgentDB("mission_supervisor")
        self._lock = threading.RLock()
        self._threads: Dict[str, threading.Thread] = {}

    def _state_key(self, mission_id: str) -> str:
        return f"mission:{mission_id}:state"

    def _events_key(self, mission_id: str) -> str:
        return f"mission:{mission_id}:events"

    def _missions_key(self) -> str:
        return "missions:index"

    def _load_index(self) -> List[Dict[str, Any]]:
        raw = self.db.get_state(self._missions_key())
        return list(raw) if isinstance(raw, list) else []

    def _save_index(self, items: List[Dict[str, Any]]) -> None:
        self.db.set_state(self._missions_key(), items)

    def _upsert_index(self, snapshot: Dict[str, Any]) -> None:
        mission_id = str(snapshot.get("mission_id") or "")
        if not mission_id:
            return
        item = {
            "mission_id": mission_id,
            "status": str(snapshot.get("status") or "unknown"),
            "created_at": snapshot.get("created_at"),
            "updated_at": snapshot.get("updated_at"),
            "request_text": snapshot.get("request_text"),
        }
        items = self._load_index()
        replaced = False
        for i, cur in enumerate(items):
            if str(cur.get("mission_id")) == mission_id:
                items[i] = item
                replaced = True
                break
        if not replaced:
            items.append(item)
        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        self._save_index(items[:200])

    def _load_events(self, mission_id: str) -> List[Dict[str, Any]]:
        raw = self.db.get_state(self._events_key(mission_id))
        return list(raw) if isinstance(raw, list) else []

    def _save_events(self, mission_id: str, events: List[Dict[str, Any]]) -> None:
        self.db.set_state(self._events_key(mission_id), events[-500:])

    def _append_event(self, mission_id: str, event_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        event = {
            "ts": _utc_now(),
            "type": event_type,
            "data": _clone_dict(data),
        }
        with self._lock:
            events = self._load_events(mission_id)
            events.append(event)
            self._save_events(mission_id, events)
        self.db.record_action("mission_event", payload=event, entity_id=mission_id)
        return event

    def _seed_graph_state(
        self,
        *,
        mission_id: str,
        request_text: str,
        initial_state: Dict[str, Any] | None,
        metadata: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        state = _clone_dict(initial_state)
        meta = _clone_dict(metadata)
        state["mission_id"] = mission_id
        state["task_id"] = str(state.get("task_id") or mission_id)
        state["request_text"] = request_text
        state.setdefault(
            "mission",
            {
                "id": mission_id,
                "request_text": request_text,
                "metadata": meta,
            },
        )
        state.setdefault("mission_phase", "preflight")
        state.setdefault("mission_status", "queued")
        state.setdefault("status", "queued")
        state.setdefault("current_step", 0)
        state.setdefault("intent", {})
        state.setdefault("selected_skill", {})
        state.setdefault("plan", [])
        state.setdefault("active_runs", {})
        state.setdefault("network_state", {})
        state.setdefault("uav_state", {})
        state.setdefault("utm_state", {})
        state.setdefault("network_state_snapshot", {})
        state.setdefault("uav_state_snapshot", {})
        state.setdefault("utm_state_snapshot", {})
        state.setdefault("mission_state_snapshot", {})
        state.setdefault("events", [])
        state.setdefault("proposed_actions", [])
        state.setdefault("applied_actions", [])
        state.setdefault("decision_log", [])
        state.setdefault("evidence_log", [])
        state.setdefault("command_bus_log", [])
        state.setdefault("task_memory", {})
        state.setdefault("rollback_context", [])
        state.setdefault("approvals", [])
        state.setdefault("pending_approvals", [])
        state.setdefault("policy_notes", [])
        state.setdefault("conflicts", [])
        return state

    def _load_state(self, mission_id: str) -> Dict[str, Any] | None:
        raw = self.db.get_state(self._state_key(mission_id))
        return dict(raw) if isinstance(raw, dict) else None

    def _save_state(self, mission_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        now = _utc_now()
        current = self._load_state(mission_id) or {}
        merged = dict(current)
        merged.update(snapshot)
        merged["mission_id"] = mission_id
        merged["updated_at"] = now
        if not merged.get("created_at"):
            merged["created_at"] = now
        self.db.set_state(self._state_key(mission_id), merged)
        self._upsert_index(merged)
        self.db.record_action("mission_state_update", payload={"status": merged.get("status")}, result=merged, entity_id=mission_id)
        return merged

    def start_mission(
        self,
        *,
        request_text: str,
        mission_id: str | None = None,
        initial_state: Dict[str, Any] | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        mid = str(mission_id or f"mission-{uuid.uuid4().hex[:10]}")
        req = str(request_text or "").strip()
        if not req:
            raise ValueError("request_text is required")

        with self._lock:
            existing = self._load_state(mid)
            if existing and str(existing.get("status")) in {"queued", "running", "stop_requested"}:
                raise ValueError(f"mission already active: {mid}")

            graph_input = self._seed_graph_state(
                mission_id=mid,
                request_text=req,
                initial_state=initial_state,
                metadata=metadata,
            )

            snapshot = self._save_state(
                mid,
                {
                    "status": "queued",
                    "request_text": req,
                    "stop_requested": False,
                    "metadata": _clone_dict(metadata),
                    "graph_input": graph_input,
                    "graph_state": _clone_dict(graph_input),
                    "error": None,
                },
            )
            self._append_event(mid, "mission_queued", {"request_text": req})

            t = threading.Thread(target=self._run_mission, args=(mid,), daemon=True, name=f"mission-runtime-{mid}")
            self._threads[mid] = t
            t.start()

            return snapshot

    def _run_mission(self, mission_id: str) -> None:
        self._save_state(mission_id, {"status": "running", "started_at": _utc_now()})
        self._append_event(mission_id, "mission_started")
        try:
            snapshot = self._load_state(mission_id) or {}
            if bool(snapshot.get("stop_requested")):
                self._save_state(mission_id, {"status": "stopped", "finished_at": _utc_now()})
                self._append_event(mission_id, "mission_stopped", {"reason": "stopped_before_invoke"})
                return

            graph_input = self._seed_graph_state(
                mission_id=mission_id,
                request_text=str(snapshot.get("request_text") or ""),
                initial_state=_clone_dict(snapshot.get("graph_input")),
                metadata=_clone_dict(snapshot.get("metadata")),
            )
            self._save_state(mission_id, {"graph_input": graph_input, "graph_state": _clone_dict(graph_input)})
            self._append_event(mission_id, "graph_invoke_started", {"task_id": graph_input.get("task_id")})
            result = mission_graph.invoke(graph_input)
            result_state = _clone_dict(result)
            self._save_state(
                mission_id,
                {
                    "status": str(result_state.get("status") or "completed"),
                    "mission_status": str(result_state.get("mission_status") or result_state.get("status") or "completed"),
                    "graph_state": result_state,
                    "finished_at": _utc_now(),
                },
            )
            self._append_event(mission_id, "graph_invoke_completed", {"status": result_state.get("status")})
        except Exception as e:
            self._save_state(
                mission_id,
                {
                    "status": "failed",
                    "error": str(e),
                    "finished_at": _utc_now(),
                },
            )
            self._append_event(mission_id, "mission_failed", {"error": str(e)})
        finally:
            with self._lock:
                self._threads.pop(mission_id, None)

    def stop_mission(self, mission_id: str, *, reason: str | None = None) -> Dict[str, Any]:
        with self._lock:
            snapshot = self._load_state(mission_id)
            if snapshot is None:
                raise KeyError(mission_id)

            status = str(snapshot.get("status") or "unknown")
            if status in {"completed", "failed", "rolled_back", "stopped"}:
                return snapshot

            thread = self._threads.get(mission_id)
            next_status = "stop_requested" if thread and thread.is_alive() else "stopped"
            updated = self._save_state(
                mission_id,
                {
                    "stop_requested": True,
                    "stop_reason": str(reason or "operator_request"),
                    "status": next_status,
                    "finished_at": _utc_now() if next_status == "stopped" else snapshot.get("finished_at"),
                },
            )
        self._append_event(mission_id, "stop_requested", {"reason": str(reason or "operator_request")})
        return updated

    def get_mission_state(self, mission_id: str) -> Dict[str, Any]:
        snapshot = self._load_state(mission_id)
        if snapshot is None:
            raise KeyError(mission_id)
        return snapshot

    def get_mission_events(self, mission_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        if self._load_state(mission_id) is None:
            raise KeyError(mission_id)
        events = self._load_events(mission_id)
        lim = max(1, min(1000, int(limit)))
        return events[-lim:]

    def get_protocol_trace(self, mission_id: str, *, limit: int = 200, include_replayed: bool = True) -> List[Dict[str, Any]]:
        snapshot = self._load_state(mission_id)
        if snapshot is None:
            raise KeyError(mission_id)
        graph_state = snapshot.get("graph_state")
        state = dict(graph_state) if isinstance(graph_state, dict) else dict(snapshot)
        bus_log = state.get("command_bus_log")
        if not isinstance(bus_log, list):
            return []
        rows: List[Dict[str, Any]] = []
        for item in bus_log:
            if not isinstance(item, dict):
                continue
            replayed = bool(item.get("replayed"))
            if (not include_replayed) and replayed:
                continue
            trace = item.get("protocol_trace")
            if not isinstance(trace, dict):
                continue
            rows.append(
                {
                    "ts": item.get("responded_at") or item.get("created_at") or item.get("requested_at"),
                    "command_id": item.get("command_id"),
                    "correlation_id": item.get("correlation_id"),
                    "mission_id": item.get("mission_id"),
                    "step_id": item.get("step_id"),
                    "domain": item.get("domain"),
                    "op": item.get("op"),
                    "status": item.get("status"),
                    "replayed": replayed,
                    "protocol_trace": trace,
                }
            )
        lim = max(1, min(2000, int(limit)))
        return rows[-lim:]

    def get_protocol_trace_mermaid(self, mission_id: str, *, limit: int = 200, include_replayed: bool = True) -> str:
        rows = self.get_protocol_trace(mission_id, limit=limit, include_replayed=include_replayed)
        lines: List[str] = ["sequenceDiagram", "autonumber"]
        if not rows:
            lines.append("participant p0 as mission_supervisor")
            lines.append("Note over p0: No protocol trace rows")
            return "\n".join(lines)

        participants: List[str] = []
        for row in rows:
            sender = _trace_sender(row)
            receiver = _trace_receiver(row)
            if sender not in participants:
                participants.append(sender)
            if receiver not in participants:
                participants.append(receiver)

        alias_map: Dict[str, str] = {}
        for idx, name in enumerate(participants):
            alias = _mermaid_alias(idx)
            alias_map[name] = alias
            lines.append(f"participant {alias} as {name}")

        for row in rows:
            sender = _trace_sender(row)
            receiver = _trace_receiver(row)
            left = alias_map.get(sender, "mission_supervisor")
            right = alias_map.get(receiver, "unknown")
            arrow = "-->>" if bool(row.get("replayed")) else "->>"
            lines.append(f"{left}{arrow}{right}: {_mermaid_label(row)}")

        return "\n".join(lines)

    def list_missions(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        lim = max(1, min(200, int(limit)))
        return self._load_index()[:lim]


MISSION_RUNTIME = MissionRuntimeService()
