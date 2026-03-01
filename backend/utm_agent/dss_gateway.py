from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from agent_db import AgentDB

from .dss_adapter import build_dss_adapter
from .operational_intents import delete_intent as dss_delete_intent
from .operational_intents import query_intents as dss_query_intents
from .operational_intents import upsert_intent as dss_upsert_intent
from .security_controls import ensure_security_state, sign_payload, verify_signature
from .subscriptions import delete_subscription as dss_delete_subscription
from .subscriptions import impacted_subscriptions as dss_impacted_subscriptions
from .subscriptions import query_subscriptions as dss_query_subscriptions
from .subscriptions import upsert_subscription as dss_upsert_subscription


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cfg() -> Dict[str, Any]:
    return {
        "mode": str(os.getenv("UTM_DSS_ADAPTER_MODE", "local") or "local").strip().lower(),
        "base_url": str(os.getenv("UTM_DSS_EXTERNAL_BASE_URL", "") or "").strip(),
        "timeout_s": float(os.getenv("UTM_DSS_EXTERNAL_TIMEOUT_S", "5.0") or 5.0),
        "failover_policy": str(os.getenv("UTM_DSS_FAILOVER_POLICY", "block") or "block").strip().lower(),  # block|degraded_local
        "require_local_conformance": str(os.getenv("UTM_DSS_REQUIRE_LOCAL_CONFORMANCE", "true") or "true").strip().lower() in {"1", "true", "yes", "on"},
        "require_intent_signatures": str(os.getenv("UTM_REQUIRE_INTENT_SIGNATURES", "true") or "true").strip().lower() in {"1", "true", "yes", "on"},
        "conformance_max_age_min": int(os.getenv("UTM_DSS_CONFORMANCE_MAX_AGE_MIN", "240") or 240),
        "issuer": str(os.getenv("UTM_LOCAL_ISSUER_ID", "utm-local") or "utm-local").strip(),
    }


def _get_map_state(db: AgentDB, key: str) -> Dict[str, Dict[str, Any]]:
    raw = db.get_state(key)
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def _set_map_state(db: AgentDB, key: str, values: Dict[str, Dict[str, Any]]) -> None:
    db.set_state(key, values)


def _get_list_state(db: AgentDB, key: str) -> List[Dict[str, Any]]:
    raw = db.get_state(key)
    if not isinstance(raw, list):
        return []
    return [dict(v) for v in raw if isinstance(v, dict)]


def _set_list_state(db: AgentDB, key: str, values: List[Dict[str, Any]]) -> None:
    db.set_state(key, values)


def _queue_notifications(db: AgentDB, notifications: List[Dict[str, Any]], *, event_type: str, source_intent_id: str = "") -> List[Dict[str, Any]]:
    if not notifications:
        return []
    queue = _get_list_state(db, "dss_notifications")
    now = _now_iso()
    out: List[Dict[str, Any]] = []
    for rec in notifications:
        if not isinstance(rec, dict):
            continue
        row = {
            "notification_id": f"notif-{os.urandom(6).hex()}",
            "event_type": str(event_type or "update"),
            "source_intent_id": str(source_intent_id or ""),
            "subscription_id": str(rec.get("subscription_id") or ""),
            "manager_uss_id": str(rec.get("manager_uss_id") or ""),
            "callback_url": str(rec.get("callback_url") or ""),
            "uss_base_url": str(rec.get("uss_base_url") or ""),
            "status": "pending",
            "created_at": now,
            "acked_at": None,
        }
        queue.append(row)
        out.append(row)
    _set_list_state(db, "dss_notifications", queue[-5000:])
    return out


def _get_security_state(db: AgentDB) -> Dict[str, Any]:
    cur = db.get_state("security_controls")
    state = ensure_security_state(dict(cur) if isinstance(cur, dict) else None)
    if not isinstance(cur, dict):
        db.set_state("security_controls", state)
    return state


def _set_security_state(db: AgentDB, state: Dict[str, Any]) -> None:
    db.set_state("security_controls", ensure_security_state(state))


def _conformance_ok(db: AgentDB, *, max_age_min: int) -> bool:
    raw = db.get_state("dss_conformance_last")
    if not isinstance(raw, dict) or raw.get("passed") is not True:
        return False
    gen = str(raw.get("generated_at") or "").strip()
    if not gen:
        return False
    try:
        dt = datetime.fromisoformat(gen.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return False
    age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
    return age_min <= float(max(1, int(max_age_min)))


def _signed_payload(payload: Dict[str, Any], *, security: Dict[str, Any], issuer: str) -> Dict[str, Any]:
    body = dict(payload or {})
    if isinstance(body.get("signature"), dict):
        return body
    sig = sign_payload({k: v for k, v in body.items() if k != "signature"}, state=security, issuer=issuer)
    body["signature"] = sig
    return body


def _verify_incoming_if_needed(payload: Dict[str, Any], *, security: Dict[str, Any], require_signatures: bool) -> Dict[str, Any]:
    body = dict(payload or {})
    manager_uss = str(body.get("manager_uss_id") or "")
    signature = body.get("signature") if isinstance(body.get("signature"), dict) else None
    external = manager_uss and not manager_uss.startswith("uss-local")
    if external and require_signatures and not isinstance(signature, dict):
        return {"ok": False, "error": "intent_signature_required"}
    if isinstance(signature, dict):
        verify = verify_signature({k: v for k, v in body.items() if k != "signature"}, signature, state=security)
        return verify
    return {"ok": True}


def _build_adapter(db: AgentDB, cfg: Dict[str, Any]):
    local_conformance = db.get_state("dss_conformance_last")
    token = str(os.getenv("UTM_DSS_EXTERNAL_TOKEN", "") or "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return build_dss_adapter(
        cfg["mode"],
        base_url=cfg["base_url"],
        timeout_s=cfg["timeout_s"],
        headers=headers,
        local_conformance=dict(local_conformance) if isinstance(local_conformance, dict) else None,
        require_local_conformance=bool(cfg["require_local_conformance"]),
    )


def _local_upsert_intent(db: AgentDB, payload: Dict[str, Any], *, security: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    verify = _verify_incoming_if_needed(payload, security=security, require_signatures=bool(cfg["require_intent_signatures"]))
    if verify.get("ok") is not True:
        return {"status": "error", "error": str(verify.get("error") or "invalid_intent_signature")}
    intents = _get_map_state(db, "dss_operational_intents")
    out = dss_upsert_intent(
        intents,
        intent_id=payload.get("intent_id"),
        manager_uss_id=str(payload.get("manager_uss_id") or "uss-local"),
        state=str(payload.get("state") or "accepted"),
        priority=str(payload.get("priority") or "normal"),
        conflict_policy=str(payload.get("conflict_policy") or "reject"),
        ovn=payload.get("ovn"),
        uss_base_url=payload.get("uss_base_url"),
        volume4d=dict(payload.get("volume4d") or {}),
        constraints=dict(payload.get("constraints") or {}),
        metadata=dict(payload.get("metadata") or {}),
    )
    if out.get("stored"):
        intent = out.get("intent") if isinstance(out.get("intent"), dict) else {}
        signed = dict(intent)
        signed["signature"] = sign_payload({k: v for k, v in signed.items() if k != "signature"}, state=security, issuer=cfg["issuer"])
        if str(signed.get("intent_id") or ""):
            intents[str(signed.get("intent_id"))] = signed
            out["intent"] = signed
        _set_map_state(db, "dss_operational_intents", intents)
    intent = out.get("intent") if isinstance(out.get("intent"), dict) else {}
    subscriptions = _get_map_state(db, "dss_subscriptions")
    event_type = "create" if int(intent.get("version", 1) or 1) <= 1 else "update"
    notifications = (
        dss_impacted_subscriptions(
            subscriptions,
            changed_volume4d=intent.get("volume4d") if isinstance(intent.get("volume4d"), dict) else dict(payload.get("volume4d") or {}),
            event_type=event_type,
        )
        if out.get("stored")
        else []
    )
    queued = _queue_notifications(db, notifications, event_type=event_type, source_intent_id=str(intent.get("intent_id") or ""))
    return {"status": "success", "result": {**out, "subscriptions_to_notify": notifications, "queued_notifications": queued}}


def gateway_upsert_operational_intent(db: AgentDB, payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _cfg()
    security = _get_security_state(db)
    body = dict(payload or {})
    body = _signed_payload(body, security=security, issuer=cfg["issuer"]) if str(body.get("manager_uss_id", "uss-local")).startswith("uss-local") else body
    if cfg["mode"] in {"local", "inmemory", "memory"}:
        return _local_upsert_intent(db, body, security=security, cfg=cfg)
    try:
        adapter = _build_adapter(db, cfg)
        out = adapter.upsert_operational_intent(body)
        if out.get("status") == "success":
            return {**out, "adapter_mode": cfg["mode"], "degraded": False}
        raise ValueError(str(out.get("error") or "external_upsert_failed"))
    except Exception as exc:
        if cfg["failover_policy"] != "degraded_local" or not _conformance_ok(db, max_age_min=cfg["conformance_max_age_min"]):
            return {"status": "error", "error": "external_dss_unavailable", "details": str(exc), "adapter_mode": cfg["mode"], "degraded": False}
        local = _local_upsert_intent(db, body, security=security, cfg=cfg)
        local["degraded"] = True
        local["adapter_mode"] = cfg["mode"]
        local["failover_reason"] = str(exc)
        return local


def gateway_query_operational_intents(db: AgentDB, payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _cfg()
    if cfg["mode"] in {"local", "inmemory", "memory"}:
        intents = _get_map_state(db, "dss_operational_intents")
        items = dss_query_intents(
            intents,
            manager_uss_id=str(payload.get("manager_uss_id")).strip() if payload.get("manager_uss_id") else None,
            states=payload.get("states") if isinstance(payload.get("states"), list) else None,
            volume4d=dict(payload.get("volume4d") or {}) if isinstance(payload.get("volume4d"), dict) else None,
        )
        return {"status": "success", "result": {"count": len(items), "items": items}}
    try:
        adapter = _build_adapter(db, cfg)
        out = adapter.query_operational_intents(dict(payload or {}))
        if out.get("status") == "success":
            return {**out, "adapter_mode": cfg["mode"], "degraded": False}
        raise ValueError(str(out.get("error") or "external_query_failed"))
    except Exception as exc:
        if cfg["failover_policy"] != "degraded_local":
            return {"status": "error", "error": "external_dss_unavailable", "details": str(exc), "adapter_mode": cfg["mode"], "degraded": False}
        intents = _get_map_state(db, "dss_operational_intents")
        items = dss_query_intents(intents, manager_uss_id=None, states=None, volume4d=None)
        return {"status": "success", "result": {"count": len(items), "items": items}, "adapter_mode": cfg["mode"], "degraded": True, "failover_reason": str(exc)}


def gateway_delete_operational_intent(db: AgentDB, intent_id: str) -> Dict[str, Any]:
    cfg = _cfg()
    if cfg["mode"] in {"local", "inmemory", "memory"}:
        intents = _get_map_state(db, "dss_operational_intents")
        before = intents.get(intent_id) if isinstance(intents.get(intent_id), dict) else {}
        out = dss_delete_intent(intents, intent_id)
        if out.get("deleted"):
            _set_map_state(db, "dss_operational_intents", intents)
        subscriptions = _get_map_state(db, "dss_subscriptions")
        notifications = (
            dss_impacted_subscriptions(
                subscriptions,
                changed_volume4d=before.get("volume4d") if isinstance(before.get("volume4d"), dict) else {},
                event_type="delete",
            )
            if out.get("deleted")
            else []
        )
        queued = _queue_notifications(db, notifications, event_type="delete", source_intent_id=str(intent_id))
        return {"status": "success", "result": {**out, "subscriptions_to_notify": notifications, "queued_notifications": queued}}
    try:
        adapter = _build_adapter(db, cfg)
        out = adapter.delete_operational_intent(intent_id)
        if out.get("status") == "success":
            return {**out, "adapter_mode": cfg["mode"], "degraded": False}
        raise ValueError(str(out.get("error") or "external_delete_failed"))
    except Exception as exc:
        if cfg["failover_policy"] != "degraded_local":
            return {"status": "error", "error": "external_dss_unavailable", "details": str(exc), "adapter_mode": cfg["mode"], "degraded": False}
        intents = _get_map_state(db, "dss_operational_intents")
        out = dss_delete_intent(intents, intent_id)
        if out.get("deleted"):
            _set_map_state(db, "dss_operational_intents", intents)
        return {"status": "success", "result": out, "adapter_mode": cfg["mode"], "degraded": True, "failover_reason": str(exc)}


def gateway_upsert_subscription(db: AgentDB, payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _cfg()
    if cfg["mode"] in {"local", "inmemory", "memory"}:
        subs = _get_map_state(db, "dss_subscriptions")
        out = dss_upsert_subscription(
            subs,
            subscription_id=payload.get("subscription_id"),
            manager_uss_id=str(payload.get("manager_uss_id") or "uss-local"),
            uss_base_url=str(payload.get("uss_base_url") or ""),
            callback_url=str(payload.get("callback_url") or ""),
            volume4d=dict(payload.get("volume4d") or {}),
            notify_for=payload.get("notify_for") if isinstance(payload.get("notify_for"), list) else None,
            expires_at=str(payload.get("expires_at") or "") or None,
            metadata=dict(payload.get("metadata") or {}),
        )
        _set_map_state(db, "dss_subscriptions", subs)
        return {"status": "success", "result": out}
    try:
        adapter = _build_adapter(db, cfg)
        out = adapter.upsert_subscription(dict(payload or {}))
        if out.get("status") == "success":
            return {**out, "adapter_mode": cfg["mode"], "degraded": False}
        raise ValueError(str(out.get("error") or "external_subscription_upsert_failed"))
    except Exception as exc:
        if cfg["failover_policy"] != "degraded_local":
            return {"status": "error", "error": "external_dss_unavailable", "details": str(exc), "adapter_mode": cfg["mode"], "degraded": False}
        subs = _get_map_state(db, "dss_subscriptions")
        out = dss_upsert_subscription(subs, subscription_id=payload.get("subscription_id"), manager_uss_id=str(payload.get("manager_uss_id") or "uss-local"), uss_base_url=str(payload.get("uss_base_url") or ""), callback_url=str(payload.get("callback_url") or ""), volume4d=dict(payload.get("volume4d") or {}), notify_for=payload.get("notify_for") if isinstance(payload.get("notify_for"), list) else None, expires_at=str(payload.get("expires_at") or "") or None, metadata=dict(payload.get("metadata") or {}))
        _set_map_state(db, "dss_subscriptions", subs)
        return {"status": "success", "result": out, "adapter_mode": cfg["mode"], "degraded": True, "failover_reason": str(exc)}


def gateway_query_subscriptions(db: AgentDB, payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _cfg()
    if cfg["mode"] in {"local", "inmemory", "memory"}:
        subs = _get_map_state(db, "dss_subscriptions")
        items = dss_query_subscriptions(
            subs,
            manager_uss_id=str(payload.get("manager_uss_id")).strip() if payload.get("manager_uss_id") else None,
            volume4d=dict(payload.get("volume4d") or {}) if isinstance(payload.get("volume4d"), dict) else None,
        )
        return {"status": "success", "result": {"count": len(items), "items": items}}
    try:
        adapter = _build_adapter(db, cfg)
        out = adapter.query_subscriptions(dict(payload or {}))
        if out.get("status") == "success":
            return {**out, "adapter_mode": cfg["mode"], "degraded": False}
        raise ValueError(str(out.get("error") or "external_subscription_query_failed"))
    except Exception as exc:
        if cfg["failover_policy"] != "degraded_local":
            return {"status": "error", "error": "external_dss_unavailable", "details": str(exc), "adapter_mode": cfg["mode"], "degraded": False}
        subs = _get_map_state(db, "dss_subscriptions")
        items = dss_query_subscriptions(subs, manager_uss_id=None, volume4d=None)
        return {"status": "success", "result": {"count": len(items), "items": items}, "adapter_mode": cfg["mode"], "degraded": True, "failover_reason": str(exc)}


def gateway_delete_subscription(db: AgentDB, subscription_id: str) -> Dict[str, Any]:
    cfg = _cfg()
    if cfg["mode"] in {"local", "inmemory", "memory"}:
        subs = _get_map_state(db, "dss_subscriptions")
        out = dss_delete_subscription(subs, subscription_id)
        if out.get("deleted"):
            _set_map_state(db, "dss_subscriptions", subs)
        return {"status": "success", "result": out}
    try:
        adapter = _build_adapter(db, cfg)
        out = adapter.delete_subscription(subscription_id)
        if out.get("status") == "success":
            return {**out, "adapter_mode": cfg["mode"], "degraded": False}
        raise ValueError(str(out.get("error") or "external_subscription_delete_failed"))
    except Exception as exc:
        if cfg["failover_policy"] != "degraded_local":
            return {"status": "error", "error": "external_dss_unavailable", "details": str(exc), "adapter_mode": cfg["mode"], "degraded": False}
        subs = _get_map_state(db, "dss_subscriptions")
        out = dss_delete_subscription(subs, subscription_id)
        if out.get("deleted"):
            _set_map_state(db, "dss_subscriptions", subs)
        return {"status": "success", "result": out, "adapter_mode": cfg["mode"], "degraded": True, "failover_reason": str(exc)}


__all__ = [
    "gateway_upsert_operational_intent",
    "gateway_query_operational_intents",
    "gateway_delete_operational_intent",
    "gateway_upsert_subscription",
    "gateway_query_subscriptions",
    "gateway_delete_subscription",
]
