from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from agent_db import AgentDB
from utm_agent import tools as utm_tools

DSS_STATE_DB = AgentDB("utm")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _retag_agent(response: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(response or {})
    out["agent"] = "dss"
    return out


def _safe_invoke(tool_obj: Any, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        params = dict(payload or {})
        raw = tool_obj.invoke(params)
        out = dict(raw) if isinstance(raw, dict) else {"status": "error", "error": "invalid_tool_response", "raw": raw}
        return _retag_agent(out)
    except Exception as exc:
        return {"status": "error", "agent": "dss", "error": "tool_invoke_failed", "details": str(exc)}


def _read_map(key: str) -> Dict[str, Dict[str, Any]]:
    raw = DSS_STATE_DB.get_state(key)
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _read_list(key: str) -> list[Dict[str, Any]]:
    raw = DSS_STATE_DB.get_state(key)
    if not isinstance(raw, list):
        return []
    return [dict(v) for v in raw if isinstance(v, dict)]


def state_snapshot() -> Dict[str, Any]:
    intents = list(_read_map("dss_operational_intents").values())
    subscriptions = list(_read_map("dss_subscriptions").values())
    participants = list(_read_map("dss_participants").values())
    notifications = _read_list("dss_notifications")
    conformance_last = DSS_STATE_DB.get_state("dss_conformance_last")
    return {
        "status": "success",
        "agent": "dss",
        "result": {
            "operationalIntentCount": len(intents),
            "subscriptionCount": len(subscriptions),
            "participantCount": len(participants),
            "pendingNotificationCount": len([n for n in notifications if str(n.get("status", "")).lower() == "pending"]),
            "operationalIntents": intents,
            "subscriptions": subscriptions,
            "participants": participants,
            "notifications": notifications,
            "lastConformance": dict(conformance_last) if isinstance(conformance_last, dict) else None,
            "updated_at": _utc_now(),
        },
    }


def upsert_operational_intent(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_upsert_operational_intent, payload)


def query_operational_intents(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_query_operational_intents, payload)


def delete_operational_intent(intent_id: str) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_delete_operational_intent, {"intent_id": str(intent_id or "")})


def upsert_subscription(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_upsert_subscription, payload)


def query_subscriptions(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_query_subscriptions, payload)


def delete_subscription(subscription_id: str) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_delete_subscription, {"subscription_id": str(subscription_id or "")})


def upsert_participant(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_upsert_participant, payload)


def query_participants(*, status: str = "") -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_query_participants, {"status": status})


def delete_participant(participant_id: str) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_delete_participant, {"participant_id": str(participant_id or "")})


def query_notifications(*, limit: int = 100, status: str = "", subscription_id: str = "") -> Dict[str, Any]:
    return _safe_invoke(
        utm_tools.utm_dss_query_notifications,
        {"limit": int(limit), "status": str(status or ""), "subscription_id": str(subscription_id or "")},
    )


def ack_notification(notification_id: str) -> Dict[str, Any]:
    return _safe_invoke(utm_tools.utm_dss_ack_notification, {"notification_id": str(notification_id or "")})


def run_local_conformance() -> Dict[str, Any]:
    out = _safe_invoke(utm_tools.utm_dss_run_local_conformance, {})
    result = _to_dict(out.get("result"))
    if str(out.get("status", "")).lower() == "success":
        stored = dict(result)
        stored.setdefault("generated_at", _utc_now())
        DSS_STATE_DB.set_state("dss_conformance_last", stored)
    return out


def get_last_conformance() -> Dict[str, Any]:
    out = _safe_invoke(utm_tools.utm_dss_get_last_conformance, {})
    result = _to_dict(out.get("result"))
    if not result:
        raw = DSS_STATE_DB.get_state("dss_conformance_last")
        out["result"] = dict(raw) if isinstance(raw, dict) else None
    return out


__all__ = [
    "state_snapshot",
    "upsert_operational_intent",
    "query_operational_intents",
    "delete_operational_intent",
    "upsert_subscription",
    "query_subscriptions",
    "delete_subscription",
    "upsert_participant",
    "query_participants",
    "delete_participant",
    "query_notifications",
    "ack_notification",
    "run_local_conformance",
    "get_last_conformance",
]
