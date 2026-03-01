from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from dss_agent import service as dss_service


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _retag(response: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(response or {})
    out["agent"] = "uss"
    return out


def state_snapshot(manager_uss_id: str = "") -> Dict[str, Any]:
    intents = dss_service.query_operational_intents({"manager_uss_id": manager_uss_id or None})
    subscriptions = dss_service.query_subscriptions({"manager_uss_id": manager_uss_id or None})
    notifications = dss_service.query_notifications(limit=200, status="", subscription_id="")
    participants = dss_service.query_participants(status="")
    return {
        "status": "success",
        "agent": "uss",
        "result": {
            "manager_uss_id": manager_uss_id or None,
            "intents": dict(intents.get("result") or {}),
            "subscriptions": dict(subscriptions.get("result") or {}),
            "notifications": dict(notifications.get("result") or {}),
            "participants": dict(participants.get("result") or {}),
            "updated_at": _utc_now(),
        },
    }


def publish_operational_intent(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retag(dss_service.upsert_operational_intent(payload))


def query_operational_intents(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retag(dss_service.query_operational_intents(payload))


def subscribe_airspace(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retag(dss_service.upsert_subscription(payload))


def query_notifications(*, limit: int = 100, status: str = "", subscription_id: str = "") -> Dict[str, Any]:
    return _retag(dss_service.query_notifications(limit=limit, status=status, subscription_id=subscription_id))


def ack_notification(notification_id: str) -> Dict[str, Any]:
    return _retag(dss_service.ack_notification(notification_id))


def register_participant(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _retag(dss_service.upsert_participant(payload))


__all__ = [
    "state_snapshot",
    "publish_operational_intent",
    "query_operational_intents",
    "subscribe_airspace",
    "query_notifications",
    "ack_notification",
    "register_participant",
]
