from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from typing import Any, Dict, List, Protocol

from .contracts import OperationalIntentContract, SubscriptionContract
from .operational_intents import delete_intent as dss_delete_intent
from .operational_intents import query_intents as dss_query_intents
from .operational_intents import upsert_intent as dss_upsert_intent
from .subscriptions import delete_subscription as dss_delete_subscription
from .subscriptions import query_subscriptions as dss_query_subscriptions
from .subscriptions import upsert_subscription as dss_upsert_subscription


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
        body = dict(payload or {})
        try:
            contract = OperationalIntentContract.from_payload(body)
            out = dss_upsert_intent(
                self.operational_intents,
                intent_id=contract.intent_id,
                manager_uss_id=contract.manager_uss_id,
                state=contract.state,
                priority=contract.priority,
                conflict_policy=contract.conflict_policy,
                ovn=contract.ovn,
                uss_base_url=contract.uss_base_url,
                volume4d=contract.volume4d.as_dict(),
                constraints=contract.constraints,
                metadata=contract.metadata,
            )
            return {"status": "success", "result": out}
        except Exception as exc:
            return {"status": "error", "error": "invalid_operational_intent_payload", "details": str(exc)}

    def query_operational_intents(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(payload or {})
        states = body.get("states")
        parsed_states: List[str] | None
        if isinstance(states, list):
            parsed_states = [str(s).strip() for s in states if str(s).strip()]
        elif isinstance(states, str):
            parsed_states = [s.strip() for s in states.split(",") if s.strip()]
        else:
            parsed_states = None
        try:
            items = dss_query_intents(
                self.operational_intents,
                manager_uss_id=str(body.get("manager_uss_id")).strip() if body.get("manager_uss_id") else None,
                states=parsed_states,
                volume4d=dict(body.get("volume4d") or {}) if isinstance(body.get("volume4d"), dict) else None,
            )
            return {"status": "success", "result": {"count": len(items), "items": items, "query": body}}
        except Exception as exc:
            return {"status": "error", "error": "invalid_operational_intent_query", "details": str(exc), "query": body}

    def delete_operational_intent(self, intent_id: str) -> Dict[str, Any]:
        out = dss_delete_intent(self.operational_intents, intent_id)
        return {"status": "success", "result": out}

    def upsert_subscription(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(payload or {})
        try:
            contract = SubscriptionContract.from_payload(body)
            out = dss_upsert_subscription(
                self.subscriptions,
                subscription_id=contract.subscription_id,
                manager_uss_id=contract.manager_uss_id,
                uss_base_url=contract.uss_base_url,
                callback_url=contract.callback_url,
                volume4d=contract.volume4d.as_dict(),
                notify_for=contract.notify_for,
                expires_at=contract.expires_at,
                metadata=contract.metadata,
            )
            return {"status": "success", "result": out}
        except Exception as exc:
            return {"status": "error", "error": "invalid_subscription_payload", "details": str(exc)}

    def query_subscriptions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(payload or {})
        try:
            items = dss_query_subscriptions(
                self.subscriptions,
                manager_uss_id=str(body.get("manager_uss_id")).strip() if body.get("manager_uss_id") else None,
                volume4d=dict(body.get("volume4d") or {}) if isinstance(body.get("volume4d"), dict) else None,
            )
            return {"status": "success", "result": {"count": len(items), "items": items, "query": body}}
        except Exception as exc:
            return {"status": "error", "error": "invalid_subscription_query", "details": str(exc), "query": body}

    def delete_subscription(self, subscription_id: str) -> Dict[str, Any]:
        out = dss_delete_subscription(self.subscriptions, subscription_id)
        return {"status": "success", "result": out}


class HttpDSSAdapter:
    """HTTP adapter for interoperating with external DSS-compatible APIs."""

    def __init__(self, *, base_url: str, timeout_s: float = 5.0, headers: Dict[str, str] | None = None) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_s = float(timeout_s)
        self.headers = dict(headers or {})

    def _request(self, method: str, path: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.base_url:
            return {"status": "error", "error": "missing_base_url"}
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if isinstance(payload, dict) else None
        req_headers = {"Accept": "application/json", **self.headers}
        if body is not None:
            req_headers["Content-Type"] = "application/json"
        req = Request(url=url, data=body, method=method.upper(), headers=req_headers)
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
                if not raw.strip():
                    return {"status": "success"}
                try:
                    data = json.loads(raw)
                    return data if isinstance(data, dict) else {"status": "success", "result": data}
                except Exception:
                    return {"status": "success", "raw": raw}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"status": "error", "error": "http_error", "http_status": int(exc.code), "details": detail}
        except URLError as exc:
            return {"status": "error", "error": "connection_error", "details": str(exc)}
        except Exception as exc:
            return {"status": "error", "error": "unexpected_error", "details": str(exc)}

    def upsert_operational_intent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/utm/dss/operational-intents", dict(payload or {}))

    def query_operational_intents(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/utm/dss/operational-intents/query", dict(payload or {}))

    def delete_operational_intent(self, intent_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/utm/dss/operational-intents/{quote(str(intent_id), safe='')}", None)

    def upsert_subscription(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/utm/dss/subscriptions", dict(payload or {}))

    def query_subscriptions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/utm/dss/subscriptions/query", dict(payload or {}))

    def delete_subscription(self, subscription_id: str) -> Dict[str, Any]:
        return self._request("DELETE", f"/api/utm/dss/subscriptions/{quote(str(subscription_id), safe='')}", None)


def build_dss_adapter(
    mode: str,
    *,
    base_url: str = "",
    timeout_s: float = 5.0,
    headers: Dict[str, str] | None = None,
    local_conformance: Dict[str, Any] | None = None,
    require_local_conformance: bool = False,
) -> DSSAdapter:
    mode_l = str(mode or "local").strip().lower()
    if mode_l in {"local", "inmemory", "memory"}:
        return InMemoryDSSAdapter()
    if mode_l in {"http", "external"}:
        if require_local_conformance:
            passed = bool(isinstance(local_conformance, dict) and local_conformance.get("passed") is True)
            if not passed:
                raise ValueError("local_conformance_not_passed")
        return HttpDSSAdapter(base_url=base_url, timeout_s=timeout_s, headers=headers)
    raise ValueError(f"unsupported_adapter_mode:{mode_l}")


__all__ = ["DSSAdapter", "InMemoryDSSAdapter", "HttpDSSAdapter", "build_dss_adapter"]
