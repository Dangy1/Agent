"""USS API as a separated domain service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError("uss_agent.api requires fastapi and pydantic") from e

from agent_db import AgentDB

from . import service


class IntentPayload(BaseModel):
    manager_uss_id: str = "uss-local"
    intent_id: Optional[str] = None
    state: str = "accepted"
    priority: str = "normal"
    conflict_policy: str = "reject"
    uss_base_url: str = ""
    volume4d: Dict[str, Any] = {}
    constraints: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class IntentQueryPayload(BaseModel):
    manager_uss_id: Optional[str] = None
    states: Optional[List[str]] = None
    volume4d: Optional[Dict[str, Any]] = None


class SubscriptionPayload(BaseModel):
    manager_uss_id: str = "uss-local"
    subscription_id: Optional[str] = None
    uss_base_url: str = ""
    callback_url: str = ""
    volume4d: Dict[str, Any] = {}
    notify_for: Optional[List[str]] = None
    expires_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ParticipantPayload(BaseModel):
    participant_id: str
    uss_base_url: str = "http://127.0.0.1:9000"
    roles: List[str] = ["uss"]
    status: str = "active"
    metadata: Optional[Dict[str, Any]] = None


app = FastAPI(title="USS Agent API")
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

USS_DB = AgentDB("uss")


def _with_sync(result: Dict[str, Any], *, action: str, payload: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    sync = USS_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)
    out = dict(result)
    out["sync"] = sync
    return out


@app.get("/api/uss/state")
def get_uss_state(manager_uss_id: str = "") -> Dict[str, Any]:
    return _with_sync(service.state_snapshot(manager_uss_id=manager_uss_id), action="uss_state", payload={"manager_uss_id": manager_uss_id})


@app.post("/api/uss/intents/publish")
def post_uss_intent_publish(payload: IntentPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(
        service.publish_operational_intent(body),
        action="uss_publish_intent",
        payload=body,
        entity_id=str(body.get("intent_id") or ""),
    )


@app.post("/api/uss/intents/query")
def post_uss_intent_query(payload: IntentQueryPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(service.query_operational_intents(body), action="uss_query_intents", payload=body)


@app.post("/api/uss/subscriptions")
def post_uss_subscription(payload: SubscriptionPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(
        service.subscribe_airspace(body),
        action="uss_subscribe_airspace",
        payload=body,
        entity_id=str(body.get("subscription_id") or ""),
    )


@app.get("/api/uss/notifications")
def get_uss_notifications(limit: int = 100, status: str = "pending", subscription_id: str = "") -> Dict[str, Any]:
    return _with_sync(
        service.query_notifications(limit=limit, status=status, subscription_id=subscription_id),
        action="uss_pull_notifications",
        payload={"limit": limit, "status": status, "subscription_id": subscription_id},
    )


@app.post("/api/uss/notifications/{notification_id}/ack")
def post_uss_notification_ack(notification_id: str) -> Dict[str, Any]:
    return _with_sync(
        service.ack_notification(notification_id),
        action="uss_ack_notification",
        payload={"notification_id": notification_id},
        entity_id=notification_id,
    )


@app.post("/api/uss/participants/register")
def post_uss_participant_register(payload: ParticipantPayload) -> Dict[str, Any]:
    body = payload.model_dump()
    return _with_sync(
        service.register_participant(body),
        action="uss_register_participant",
        payload=body,
        entity_id=body["participant_id"],
    )


@app.get("/api/uss/sync")
def get_uss_sync(limit_actions: int = 20) -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "sync": USS_DB.get_sync(),
            "recentActions": USS_DB.recent_actions(limit_actions),
        },
    }
