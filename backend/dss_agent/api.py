"""DSS API as a separated domain service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError("dss_agent.api requires fastapi and pydantic") from e

from agent_db import AgentDB

from . import service


class VolumeQueryPayload(BaseModel):
    manager_uss_id: Optional[str] = None
    states: Optional[List[str]] = None
    volume4d: Optional[Dict[str, Any]] = None


class IntentPayload(BaseModel):
    intent_id: Optional[str] = None
    manager_uss_id: str = "uss-local"
    state: str = "accepted"
    priority: str = "normal"
    conflict_policy: str = "reject"
    uss_base_url: str = ""
    volume4d: Dict[str, Any] = {}
    constraints: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class SubscriptionPayload(BaseModel):
    subscription_id: Optional[str] = None
    manager_uss_id: str = "uss-local"
    uss_base_url: str = ""
    callback_url: str = ""
    volume4d: Dict[str, Any] = {}
    notify_for: Optional[List[str]] = None
    expires_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class SubscriptionQueryPayload(BaseModel):
    manager_uss_id: Optional[str] = None
    volume4d: Optional[Dict[str, Any]] = None


class ParticipantPayload(BaseModel):
    participant_id: str
    uss_base_url: str = "http://127.0.0.1:9000"
    roles: List[str] = ["uss"]
    status: str = "active"
    metadata: Optional[Dict[str, Any]] = None


app = FastAPI(title="DSS Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DSS_DB = AgentDB("dss")


def _with_sync(result: Dict[str, Any], *, action: str, payload: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    sync = DSS_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)
    out = dict(result)
    out["sync"] = sync
    return out


@app.get("/api/dss/state")
def get_dss_state() -> Dict[str, Any]:
    return _with_sync(service.state_snapshot(), action="dss_state")


@app.post("/api/dss/operational-intents")
def post_dss_operational_intent(payload: IntentPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(
        service.upsert_operational_intent(body),
        action="dss_upsert_operational_intent",
        payload=body,
        entity_id=str(body.get("intent_id") or ""),
    )


@app.put("/api/dss/operational-intents/{intent_id}")
def put_dss_operational_intent(intent_id: str, payload: IntentPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    body["intent_id"] = intent_id
    return _with_sync(
        service.upsert_operational_intent(body),
        action="dss_put_operational_intent",
        payload=body,
        entity_id=intent_id,
    )


@app.post("/api/dss/operational-intents/query")
def post_dss_operational_intent_query(payload: VolumeQueryPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(service.query_operational_intents(body), action="dss_query_operational_intents", payload=body)


@app.delete("/api/dss/operational-intents/{intent_id}")
def delete_dss_operational_intent(intent_id: str) -> Dict[str, Any]:
    return _with_sync(
        service.delete_operational_intent(intent_id),
        action="dss_delete_operational_intent",
        payload={"intent_id": intent_id},
        entity_id=intent_id,
    )


@app.post("/api/dss/subscriptions")
def post_dss_subscription(payload: SubscriptionPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(
        service.upsert_subscription(body),
        action="dss_upsert_subscription",
        payload=body,
        entity_id=str(body.get("subscription_id") or ""),
    )


@app.post("/api/dss/subscriptions/query")
def post_dss_subscription_query(payload: SubscriptionQueryPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(service.query_subscriptions(body), action="dss_query_subscriptions", payload=body)


@app.delete("/api/dss/subscriptions/{subscription_id}")
def delete_dss_subscription(subscription_id: str) -> Dict[str, Any]:
    return _with_sync(
        service.delete_subscription(subscription_id),
        action="dss_delete_subscription",
        payload={"subscription_id": subscription_id},
        entity_id=subscription_id,
    )


@app.get("/api/dss/participants")
def get_dss_participants(status: str = "") -> Dict[str, Any]:
    return _with_sync(service.query_participants(status=status), action="dss_query_participants", payload={"status": status})


@app.post("/api/dss/participants")
def post_dss_participant(payload: ParticipantPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(
        service.upsert_participant(body),
        action="dss_upsert_participant",
        payload=body,
        entity_id=body["participant_id"],
    )


@app.delete("/api/dss/participants/{participant_id}")
def delete_dss_participant(participant_id: str) -> Dict[str, Any]:
    return _with_sync(
        service.delete_participant(participant_id),
        action="dss_delete_participant",
        payload={"participant_id": participant_id},
        entity_id=participant_id,
    )


@app.get("/api/dss/notifications")
def get_dss_notifications(limit: int = 100, status: str = "", subscription_id: str = "") -> Dict[str, Any]:
    return _with_sync(
        service.query_notifications(limit=limit, status=status, subscription_id=subscription_id),
        action="dss_query_notifications",
        payload={"limit": limit, "status": status, "subscription_id": subscription_id},
    )


@app.post("/api/dss/notifications/{notification_id}/ack")
def post_dss_notification_ack(notification_id: str) -> Dict[str, Any]:
    return _with_sync(
        service.ack_notification(notification_id),
        action="dss_ack_notification",
        payload={"notification_id": notification_id},
        entity_id=notification_id,
    )


@app.post("/api/dss/conformance/run")
def post_dss_conformance_run() -> Dict[str, Any]:
    return _with_sync(service.run_local_conformance(), action="dss_run_local_conformance")


@app.get("/api/dss/conformance/last")
def get_dss_conformance_last() -> Dict[str, Any]:
    return _with_sync(service.get_last_conformance(), action="dss_get_last_conformance")


@app.get("/api/dss/sync")
def get_dss_sync(limit_actions: int = 20) -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "sync": DSS_DB.get_sync(),
            "recentActions": DSS_DB.recent_actions(limit_actions),
        },
    }
