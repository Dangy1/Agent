from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _clean_str(value: Any, default: str = "") -> str:
    out = str(value or "").strip()
    return out if out else default


def _pair(raw: Any, *, fallback: tuple[float, float]) -> tuple[float, float]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        a = float(raw[0])
        b = float(raw[1])
        return (min(a, b), max(a, b))
    return fallback


@dataclass(frozen=True)
class Volume4DContract:
    x: tuple[float, float]
    y: tuple[float, float]
    z: tuple[float, float]
    time_start: str
    time_end: str

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Volume4DContract":
        body = dict(payload or {})
        x = _pair(body.get("x"), fallback=(-1e9, 1e9))
        y = _pair(body.get("y"), fallback=(-1e9, 1e9))
        z = _pair(body.get("z"), fallback=(0.0, 120.0))
        start_dt = _parse_dt(body.get("time_start")) or datetime.now(timezone.utc)
        end_dt = _parse_dt(body.get("time_end")) or (start_dt + timedelta(minutes=20))
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=1)
        return cls(
            x=x,
            y=y,
            z=z,
            time_start=start_dt.isoformat().replace("+00:00", "Z"),
            time_end=end_dt.isoformat().replace("+00:00", "Z"),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "x": [self.x[0], self.x[1]],
            "y": [self.y[0], self.y[1]],
            "z": [self.z[0], self.z[1]],
            "time_start": self.time_start,
            "time_end": self.time_end,
        }


@dataclass(frozen=True)
class OperationalIntentContract:
    intent_id: str
    manager_uss_id: str
    state: str
    priority: str
    conflict_policy: str
    ovn: Optional[str]
    uss_base_url: str
    volume4d: Volume4DContract
    constraints: Dict[str, Any]
    metadata: Dict[str, Any]

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "OperationalIntentContract":
        body = dict(payload or {})
        return cls(
            intent_id=_clean_str(body.get("intent_id"), default=f"oi-{uuid4().hex[:12]}"),
            manager_uss_id=_clean_str(body.get("manager_uss_id"), default="uss-local"),
            state=_clean_str(body.get("state"), default="accepted").lower(),
            priority=_clean_str(body.get("priority"), default="normal").lower(),
            conflict_policy=_clean_str(body.get("conflict_policy"), default="reject").lower(),
            ovn=_clean_str(body.get("ovn")) or None,
            uss_base_url=_clean_str(body.get("uss_base_url")),
            volume4d=Volume4DContract.from_payload(dict(body.get("volume4d") or {})),
            constraints=dict(body.get("constraints") or {}),
            metadata=dict(body.get("metadata") or {}),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "manager_uss_id": self.manager_uss_id,
            "state": self.state,
            "priority": self.priority,
            "conflict_policy": self.conflict_policy,
            "ovn": self.ovn,
            "uss_base_url": self.uss_base_url,
            "volume4d": self.volume4d.as_dict(),
            "constraints": dict(self.constraints),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SubscriptionContract:
    subscription_id: str
    manager_uss_id: str
    uss_base_url: str
    callback_url: str
    volume4d: Volume4DContract
    notify_for: List[str]
    expires_at: Optional[str]
    metadata: Dict[str, Any]

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "SubscriptionContract":
        body = dict(payload or {})
        notify_for = [str(v).strip().lower() for v in (body.get("notify_for") or []) if str(v).strip()]
        return cls(
            subscription_id=_clean_str(body.get("subscription_id"), default=f"sub-{uuid4().hex[:12]}"),
            manager_uss_id=_clean_str(body.get("manager_uss_id"), default="uss-local"),
            uss_base_url=_clean_str(body.get("uss_base_url")),
            callback_url=_clean_str(body.get("callback_url")),
            volume4d=Volume4DContract.from_payload(dict(body.get("volume4d") or {})),
            notify_for=notify_for or ["create", "update", "delete"],
            expires_at=_clean_str(body.get("expires_at")) or None,
            metadata=dict(body.get("metadata") or {}),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "manager_uss_id": self.manager_uss_id,
            "uss_base_url": self.uss_base_url,
            "callback_url": self.callback_url,
            "volume4d": self.volume4d.as_dict(),
            "notify_for": list(self.notify_for),
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CommandAuditEnvelope:
    command_id: str
    correlation_id: str
    mission_id: str
    step_id: str
    domain: str
    op: str
    created_at: str

    @classmethod
    def from_command(cls, command: Dict[str, Any], state: Dict[str, Any]) -> "CommandAuditEnvelope":
        mission_id = _clean_str(state.get("mission_id"), default="mission-local")
        step_id = _clean_str(command.get("step_id"), default="step-unknown")
        return cls(
            command_id=f"cmd-{uuid4().hex[:12]}",
            correlation_id=_clean_str(state.get("task_id"), default=mission_id),
            mission_id=mission_id,
            step_id=step_id,
            domain=_clean_str(command.get("domain")),
            op=_clean_str(command.get("op")),
            created_at=_now_iso(),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "correlation_id": self.correlation_id,
            "mission_id": self.mission_id,
            "step_id": self.step_id,
            "domain": self.domain,
            "op": self.op,
            "created_at": self.created_at,
        }


__all__ = [
    "Volume4DContract",
    "OperationalIntentContract",
    "SubscriptionContract",
    "CommandAuditEnvelope",
]
