from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain.tools import tool

from . import service


@tool
def dss_state() -> Dict[str, Any]:
    """Get a DSS state snapshot: intents, subscriptions, participants, notifications."""
    return service.state_snapshot()


@tool
def dss_upsert_operational_intent(
    intent_id: str = "",
    manager_uss_id: str = "uss-local",
    state: str = "accepted",
    priority: str = "normal",
    conflict_policy: str = "reject",
    uss_base_url: str = "",
    volume4d: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or update an operational intent in DSS."""
    return service.upsert_operational_intent(
        {
            "intent_id": intent_id or None,
            "manager_uss_id": manager_uss_id,
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
def dss_query_operational_intents(
    manager_uss_id: str = "",
    states: Optional[List[str]] = None,
    volume4d: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Query DSS operational intents."""
    return service.query_operational_intents(
        {
            "manager_uss_id": manager_uss_id or None,
            "states": states,
            "volume4d": dict(volume4d or {}) if isinstance(volume4d, dict) else None,
        }
    )


@tool
def dss_delete_operational_intent(intent_id: str) -> Dict[str, Any]:
    """Delete a DSS operational intent."""
    return service.delete_operational_intent(intent_id)


@tool
def dss_upsert_subscription(
    subscription_id: str = "",
    manager_uss_id: str = "uss-local",
    uss_base_url: str = "",
    callback_url: str = "",
    volume4d: Optional[Dict[str, Any]] = None,
    notify_for: Optional[List[str]] = None,
    expires_at: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or update a DSS subscription."""
    return service.upsert_subscription(
        {
            "subscription_id": subscription_id or None,
            "manager_uss_id": manager_uss_id,
            "uss_base_url": uss_base_url,
            "callback_url": callback_url,
            "volume4d": dict(volume4d or {}),
            "notify_for": notify_for,
            "expires_at": expires_at or None,
            "metadata": dict(metadata or {}),
        }
    )


@tool
def dss_query_subscriptions(
    manager_uss_id: str = "",
    volume4d: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Query DSS subscriptions."""
    return service.query_subscriptions(
        {"manager_uss_id": manager_uss_id or None, "volume4d": dict(volume4d or {}) if isinstance(volume4d, dict) else None}
    )


@tool
def dss_delete_subscription(subscription_id: str) -> Dict[str, Any]:
    """Delete a DSS subscription."""
    return service.delete_subscription(subscription_id)


@tool
def dss_upsert_participant(
    participant_id: str,
    uss_base_url: str = "http://127.0.0.1:9000",
    roles: Optional[List[str]] = None,
    status: str = "active",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Register or update a DSS participant."""
    return service.upsert_participant(
        {
            "participant_id": participant_id,
            "uss_base_url": uss_base_url,
            "roles": roles,
            "status": status,
            "metadata": dict(metadata or {}),
        }
    )


@tool
def dss_query_participants(status: str = "") -> Dict[str, Any]:
    """Query DSS participants."""
    return service.query_participants(status=status)


@tool
def dss_delete_participant(participant_id: str) -> Dict[str, Any]:
    """Delete a DSS participant."""
    return service.delete_participant(participant_id)


@tool
def dss_query_notifications(limit: int = 100, status: str = "", subscription_id: str = "") -> Dict[str, Any]:
    """Query DSS notifications."""
    return service.query_notifications(limit=limit, status=status, subscription_id=subscription_id)


@tool
def dss_ack_notification(notification_id: str) -> Dict[str, Any]:
    """Acknowledge a DSS notification."""
    return service.ack_notification(notification_id)


@tool
def dss_run_local_conformance() -> Dict[str, Any]:
    """Run local DSS conformance checks."""
    return service.run_local_conformance()


@tool
def dss_conformance_last() -> Dict[str, Any]:
    """Read latest DSS conformance result."""
    return service.get_last_conformance()


TOOLS = [
    dss_state,
    dss_upsert_operational_intent,
    dss_query_operational_intents,
    dss_delete_operational_intent,
    dss_upsert_subscription,
    dss_query_subscriptions,
    dss_delete_subscription,
    dss_upsert_participant,
    dss_query_participants,
    dss_delete_participant,
    dss_query_notifications,
    dss_ack_notification,
    dss_run_local_conformance,
    dss_conformance_last,
]


__all__ = [
    "TOOLS",
    "dss_state",
    "dss_upsert_operational_intent",
    "dss_query_operational_intents",
    "dss_delete_operational_intent",
    "dss_upsert_subscription",
    "dss_query_subscriptions",
    "dss_delete_subscription",
    "dss_upsert_participant",
    "dss_query_participants",
    "dss_delete_participant",
    "dss_query_notifications",
    "dss_ack_notification",
    "dss_run_local_conformance",
    "dss_conformance_last",
]
