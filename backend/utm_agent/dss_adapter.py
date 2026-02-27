from __future__ import annotations

from typing import Any, Dict, List, Protocol


class DSSAdapter(Protocol):
    def upsert_operational_intent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def query_operational_intents(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def delete_operational_intent(self, intent_id: str) -> Dict[str, Any]:
        ...

    def upsert_subscription(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def query_subscriptions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def delete_subscription(self, subscription_id: str) -> Dict[str, Any]:
        ...


class InMemoryDSSAdapter:
    """Simple adapter contract for wiring local DSS primitives before external DSS sync."""

    def __init__(self) -> None:
        self.operational_intents: Dict[str, Dict[str, Any]] = {}
        self.subscriptions: Dict[str, Dict[str, Any]] = {}

    def upsert_operational_intent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "error", "error": "not_implemented", "payload": payload}

    def query_operational_intents(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "success", "result": {"items": list(self.operational_intents.values()), "query": dict(payload or {})}}

    def delete_operational_intent(self, intent_id: str) -> Dict[str, Any]:
        existed = self.operational_intents.pop(str(intent_id), None)
        return {"status": "success", "result": {"deleted": existed is not None, "intent_id": intent_id}}

    def upsert_subscription(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "error", "error": "not_implemented", "payload": payload}

    def query_subscriptions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "success", "result": {"items": list(self.subscriptions.values()), "query": dict(payload or {})}}

    def delete_subscription(self, subscription_id: str) -> Dict[str, Any]:
        existed = self.subscriptions.pop(str(subscription_id), None)
        return {"status": "success", "result": {"deleted": existed is not None, "subscription_id": subscription_id}}


__all__ = ["DSSAdapter", "InMemoryDSSAdapter"]

