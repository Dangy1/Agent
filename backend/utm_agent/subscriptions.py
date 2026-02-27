from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from .operational_intents import normalize_volume4d, volume4d_overlaps


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _event_types(raw: Iterable[str] | None) -> List[str]:
    allowed = {"create", "update", "delete"}
    parsed = [str(v).strip().lower() for v in (raw or []) if str(v).strip()]
    out = [v for v in parsed if v in allowed]
    return out or ["create", "update", "delete"]


def upsert_subscription(
    subscriptions: Dict[str, Dict[str, Any]],
    *,
    subscription_id: str | None = None,
    manager_uss_id: str = "uss-local",
    uss_base_url: str = "",
    callback_url: str = "",
    volume4d: Dict[str, Any],
    notify_for: List[str] | None = None,
    expires_at: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    sid = str(subscription_id or f"sub-{uuid4().hex[:12]}")
    prev = subscriptions.get(sid) if isinstance(subscriptions.get(sid), dict) else None
    version = int(prev.get("version", 0) or 0) + 1 if prev else 1
    rec = {
        "subscription_id": sid,
        "manager_uss_id": str(manager_uss_id or (prev.get("manager_uss_id") if prev else "uss-local")),
        "uss_base_url": str(uss_base_url or (prev.get("uss_base_url") if prev else "") or ""),
        "callback_url": str(callback_url or (prev.get("callback_url") if prev else "") or ""),
        "volume4d": normalize_volume4d(volume4d),
        "notify_for": _event_types(notify_for if notify_for is not None else (prev.get("notify_for") if prev else None)),
        "expires_at": str(expires_at or (prev.get("expires_at") if prev else "") or ""),
        "metadata": dict(metadata) if isinstance(metadata, dict) else (dict(prev.get("metadata")) if isinstance(prev, dict) and isinstance(prev.get("metadata"), dict) else {}),
        "updated_at": _now_iso(),
        "version": version,
    }
    subscriptions[sid] = rec
    return {"status": "success", "subscription": rec}


def delete_subscription(subscriptions: Dict[str, Dict[str, Any]], subscription_id: str) -> Dict[str, Any]:
    sid = str(subscription_id or "").strip()
    if not sid:
        return {"deleted": False, "error": "subscription_id_required"}
    existed = subscriptions.pop(sid, None)
    return {"deleted": existed is not None, "subscription_id": sid, "subscription": dict(existed) if isinstance(existed, dict) else None}


def query_subscriptions(
    subscriptions: Dict[str, Dict[str, Any]],
    *,
    manager_uss_id: str | None = None,
    volume4d: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    query_volume = normalize_volume4d(volume4d) if isinstance(volume4d, dict) else None
    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    for value in subscriptions.values():
        if not isinstance(value, dict):
            continue
        if manager_uss_id and str(value.get("manager_uss_id") or "") != str(manager_uss_id):
            continue
        expires = _parse_dt(value.get("expires_at"))
        if expires is not None and expires < now:
            continue
        if query_volume is not None:
            rec_volume = value.get("volume4d")
            if not isinstance(rec_volume, dict) or not volume4d_overlaps(query_volume, rec_volume):
                continue
        out.append(dict(value))
    out.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return out


def impacted_subscriptions(
    subscriptions: Dict[str, Dict[str, Any]],
    *,
    changed_volume4d: Dict[str, Any],
    event_type: str,
) -> List[Dict[str, Any]]:
    evt = str(event_type or "").strip().lower()
    if evt not in {"create", "update", "delete"}:
        return []
    query_volume = normalize_volume4d(changed_volume4d)
    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    for value in subscriptions.values():
        if not isinstance(value, dict):
            continue
        expires = _parse_dt(value.get("expires_at"))
        if expires is not None and expires < now:
            continue
        notify_for = _event_types(value.get("notify_for") if isinstance(value.get("notify_for"), list) else None)
        if evt not in notify_for:
            continue
        rec_volume = value.get("volume4d")
        if not isinstance(rec_volume, dict):
            continue
        if not volume4d_overlaps(query_volume, rec_volume):
            continue
        out.append(
            {
                "subscription_id": str(value.get("subscription_id") or ""),
                "manager_uss_id": str(value.get("manager_uss_id") or ""),
                "callback_url": str(value.get("callback_url") or ""),
                "uss_base_url": str(value.get("uss_base_url") or ""),
                "notify_for": notify_for,
            }
        )
    return out

