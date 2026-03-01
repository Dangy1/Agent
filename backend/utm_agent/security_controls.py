from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _hmac_sign(secret: str, payload: Dict[str, Any]) -> str:
    return hmac.new(str(secret).encode("utf-8"), _canonical_json_bytes(payload), hashlib.sha256).hexdigest()


def _default_tokens_from_env() -> Dict[str, List[str]]:
    raw_map = os.getenv("UTM_SERVICE_TOKENS_JSON", "").strip()
    if raw_map:
        try:
            parsed = json.loads(raw_map)
            if isinstance(parsed, dict):
                out: Dict[str, List[str]] = {}
                for token, roles in parsed.items():
                    if not str(token).strip():
                        continue
                    if isinstance(roles, list):
                        vals = [str(r).strip() for r in roles if str(r).strip()]
                    else:
                        vals = [str(roles).strip()] if str(roles).strip() else []
                    out[str(token).strip()] = vals or ["read"]
                if out:
                    return out
        except Exception:
            pass
    single = os.getenv("UTM_SERVICE_TOKEN", "").strip()
    if single:
        return {single: ["admin", "read", "utm_write", "dss_read", "dss_write", "security_admin", "compliance_admin", "conformance_run"]}
    # Local default for development; production should override.
    return {"local-dev-token": ["admin", "read", "utm_write", "dss_read", "dss_write", "security_admin", "compliance_admin", "conformance_run"]}


def ensure_security_state(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    now = _now_iso()
    state = dict(raw or {})
    trust = dict(state.get("trust_store") or {}) if isinstance(state.get("trust_store"), dict) else {}
    keys = list(trust.get("keys") or []) if isinstance(trust.get("keys"), list) else []
    if not keys:
        default_secret = os.getenv("UTM_LOCAL_SIGNING_SECRET", "").strip() or f"dev-{uuid4().hex}"
        keys = [
            {
                "issuer": "utm-local",
                "key_id": f"key-{uuid4().hex[:10]}",
                "alg": "HS256",
                "secret": default_secret,
                "status": "active",
                "created_at": now,
                "rotated_at": None,
            }
        ]
    policy = dict(state.get("key_rotation_policy") or {}) if isinstance(state.get("key_rotation_policy"), dict) else {}
    policy.setdefault("max_age_days", 30)
    policy.setdefault("overlap_days", 7)
    policy.setdefault("auto_rotate", False)
    policy.setdefault("last_rotation_at", None)
    tokens = dict(state.get("service_tokens") or {}) if isinstance(state.get("service_tokens"), dict) else {}
    if not tokens:
        tokens = _default_tokens_from_env()
    state["trust_store"] = {"keys": keys, "updated_at": now}
    state["key_rotation_policy"] = policy
    state["service_tokens"] = {str(k): [str(r).strip() for r in (v or []) if str(r).strip()] for k, v in tokens.items() if str(k).strip()}
    state["updated_at"] = now
    return state


def _active_key(state: Dict[str, Any], *, issuer: str) -> Dict[str, Any] | None:
    trust = state.get("trust_store") if isinstance(state.get("trust_store"), dict) else {}
    keys = trust.get("keys") if isinstance(trust.get("keys"), list) else []
    for rec in keys:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("issuer", "")) == str(issuer) and str(rec.get("status", "")).lower() == "active":
            return rec
    return None


def sign_payload(payload: Dict[str, Any], *, state: Dict[str, Any], issuer: str = "utm-local") -> Dict[str, Any]:
    key = _active_key(state, issuer=issuer)
    if not isinstance(key, dict):
        raise ValueError("active_signing_key_not_found")
    signature = _hmac_sign(str(key.get("secret", "")), payload)
    return {
        "alg": "HS256",
        "issuer": str(issuer),
        "key_id": str(key.get("key_id", "")),
        "signed_at": _now_iso(),
        "signature": signature,
    }


def verify_signature(payload: Dict[str, Any], signature: Dict[str, Any], *, state: Dict[str, Any]) -> Dict[str, Any]:
    sig = dict(signature or {})
    issuer = str(sig.get("issuer", "")).strip()
    key_id = str(sig.get("key_id", "")).strip()
    given = str(sig.get("signature", "")).strip()
    if not issuer or not key_id or not given:
        return {"ok": False, "error": "signature_fields_missing"}
    trust = state.get("trust_store") if isinstance(state.get("trust_store"), dict) else {}
    keys = trust.get("keys") if isinstance(trust.get("keys"), list) else []
    match = None
    for rec in keys:
        if not isinstance(rec, dict):
            continue
        if str(rec.get("issuer", "")) == issuer and str(rec.get("key_id", "")) == key_id and str(rec.get("status", "")).lower() in {"active", "retiring"}:
            match = rec
            break
    if not isinstance(match, dict):
        return {"ok": False, "error": "untrusted_key"}
    expect = _hmac_sign(str(match.get("secret", "")), payload)
    if not hmac.compare_digest(given, expect):
        return {"ok": False, "error": "signature_mismatch"}
    return {"ok": True, "issuer": issuer, "key_id": key_id}


def register_peer_key(
    state: Dict[str, Any],
    *,
    issuer: str,
    key_id: str,
    secret: str,
    status: str = "active",
) -> Dict[str, Any]:
    out = ensure_security_state(state)
    trust = out.get("trust_store") if isinstance(out.get("trust_store"), dict) else {}
    keys = list(trust.get("keys") or []) if isinstance(trust.get("keys"), list) else []
    keys = [
        rec
        for rec in keys
        if not (
            isinstance(rec, dict)
            and str(rec.get("issuer", "")) == str(issuer)
            and str(rec.get("key_id", "")) == str(key_id)
        )
    ]
    keys.append(
        {
            "issuer": str(issuer),
            "key_id": str(key_id),
            "alg": "HS256",
            "secret": str(secret),
            "status": str(status or "active"),
            "created_at": _now_iso(),
            "rotated_at": None,
        }
    )
    out["trust_store"] = {"keys": keys, "updated_at": _now_iso()}
    return out


def rotate_signing_key(state: Dict[str, Any], *, issuer: str = "utm-local") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    out = ensure_security_state(state)
    trust = out.get("trust_store") if isinstance(out.get("trust_store"), dict) else {}
    keys = list(trust.get("keys") or []) if isinstance(trust.get("keys"), list) else []
    now = _now_iso()
    for rec in keys:
        if isinstance(rec, dict) and str(rec.get("issuer", "")) == str(issuer) and str(rec.get("status", "")).lower() == "active":
            rec["status"] = "retiring"
            rec["rotated_at"] = now
    new_key = {
        "issuer": str(issuer),
        "key_id": f"key-{uuid4().hex[:10]}",
        "alg": "HS256",
        "secret": f"rot-{uuid4().hex}",
        "status": "active",
        "created_at": now,
        "rotated_at": None,
    }
    keys.append(new_key)
    out["trust_store"] = {"keys": keys, "updated_at": now}
    policy = out.get("key_rotation_policy") if isinstance(out.get("key_rotation_policy"), dict) else {}
    policy["last_rotation_at"] = now
    out["key_rotation_policy"] = policy
    out["updated_at"] = now
    return out, new_key


def authorize_service_request(
    *,
    path: str,
    method: str,
    authorization_header: str,
    state: Dict[str, Any],
    enforce: bool,
) -> Dict[str, Any]:
    if not enforce:
        return {"ok": True, "token": "no_enforce", "roles": ["admin"]}
    required_role = "read"
    p = str(path or "")
    m = str(method or "GET").upper()
    if p.startswith("/api/utm/security"):
        required_role = "security_admin"
    elif p.startswith("/api/utm/dss"):
        required_role = "dss_write" if m in {"POST", "PUT", "DELETE"} else "dss_read"
    elif p.startswith("/api/utm/release") or p.startswith("/api/utm/certification"):
        required_role = "compliance_admin"
    elif p.startswith("/api/utm/conformance/run-local"):
        required_role = "conformance_run"
    elif p.startswith("/api/utm") and m in {"POST", "PUT", "DELETE"}:
        required_role = "utm_write"

    token = ""
    raw = str(authorization_header or "")
    if raw.lower().startswith("bearer "):
        token = raw[7:].strip()
    tokens = state.get("service_tokens") if isinstance(state.get("service_tokens"), dict) else {}
    roles = tokens.get(token) if isinstance(tokens.get(token), list) else []
    role_set = {str(r).strip() for r in roles if str(r).strip()}
    allowed = bool(token and (required_role in role_set or "admin" in role_set))
    if not token:
        return {"ok": False, "error": "missing_bearer_token", "required_role": required_role}
    if not allowed:
        return {"ok": False, "error": "insufficient_role", "required_role": required_role}
    return {"ok": True, "token": token, "roles": sorted(role_set), "required_role": required_role}


__all__ = [
    "ensure_security_state",
    "sign_payload",
    "verify_signature",
    "register_peer_key",
    "rotate_signing_key",
    "authorize_service_request",
]
