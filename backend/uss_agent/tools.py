from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain.tools import tool

from . import service


@tool
def uss_state(manager_uss_id: str = "") -> Dict[str, Any]:
    """Get USS-scoped view of DSS intents, subscriptions, notifications, and participants."""
    return service.state_snapshot(manager_uss_id=manager_uss_id)


@tool
def uss_publish_intent(
    manager_uss_id: str = "uss-local",
    intent_id: str = "",
    state: str = "accepted",
    priority: str = "normal",
    conflict_policy: str = "reject",
    uss_base_url: str = "",
    volume4d: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Publish or update a USS-managed operational intent in DSS."""
    return service.publish_operational_intent(
        {
            "manager_uss_id": manager_uss_id,
            "intent_id": intent_id or None,
            "state": state,
            "priority": priority,
            "conflict_policy": conflict_policy,
            "uss_base_url": uss_base_url,
            "volume4d": dict(volume4d or {}),
            "constraints": dict(constraints or {}),
            "metadata": dict(metadata or {}),
        }
    )


@tool
def uss_query_intents(
    manager_uss_id: str = "",
    states: Optional[List[str]] = None,
    volume4d: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Query USS-visible operational intents from DSS."""
    return service.query_operational_intents(
        {
            "manager_uss_id": manager_uss_id or None,
            "states": states,
            "volume4d": dict(volume4d or {}) if isinstance(volume4d, dict) else None,
        }
    )


@tool
def uss_subscribe_airspace(
    manager_uss_id: str = "uss-local",
    subscription_id: str = "",
    uss_base_url: str = "",
    callback_url: str = "",
    volume4d: Optional[Dict[str, Any]] = None,
    notify_for: Optional[List[str]] = None,
    expires_at: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or update a USS-managed DSS subscription."""
    return service.subscribe_airspace(
        {
            "manager_uss_id": manager_uss_id,
            "subscription_id": subscription_id or None,
            "uss_base_url": uss_base_url,
            "callback_url": callback_url,
            "volume4d": dict(volume4d or {}),
            "notify_for": notify_for,
            "expires_at": expires_at or None,
            "metadata": dict(metadata or {}),
        }
    )


@tool
def uss_pull_notifications(limit: int = 100, status: str = "pending", subscription_id: str = "") -> Dict[str, Any]:
    """Read notifications for USS callback handling."""
    return service.query_notifications(limit=limit, status=status, subscription_id=subscription_id)


@tool
def uss_ack_notification(notification_id: str) -> Dict[str, Any]:
    """Acknowledge a USS notification."""
    return service.ack_notification(notification_id)


@tool
def uss_register_participant(
    participant_id: str,
    uss_base_url: str = "http://127.0.0.1:9000",
    roles: Optional[List[str]] = None,
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register this USS identity in the DSS participant set."""
    return service.register_participant(
        {
            "participant_id": participant_id,
            "uss_base_url": uss_base_url,
            "roles": roles or ["uss"],
            "status": status,
            "metadata": dict(metadata or {}),
        }
    )


TOOLS = [
    uss_state,
    uss_publish_intent,
    uss_query_intents,
    uss_subscribe_airspace,
    uss_pull_notifications,
    uss_ack_notification,
    uss_register_participant,
]


__all__ = [
    "TOOLS",
    "uss_state",
    "uss_publish_intent",
    "uss_query_intents",
    "uss_subscribe_airspace",
    "uss_pull_notifications",
    "uss_ack_notification",
    "uss_register_participant",
]
