#!/usr/bin/env python3
"""
mcp_uav_utm_strict_ops.py (STDIO MCP server)

Strict-operations MCP tools for UTM-focused workflows:
- DSS operations
- Conformance operations
- Security operations

Environment:
  UTM_BASE_URL=http://127.0.0.1:8021
  UTM_SERVICE_TOKEN=local-dev-token
  MCP_UAV_UTM_TIMEOUT_S=6.0
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

try:
    from mcp.server.fastmcp import FastMCP
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "mcp_uav_utm_strict_ops.py requires the `mcp` package. "
        "Install it in the active environment before running this server."
    ) from e


logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp-uav-utm-strict-ops")

mcp = FastMCP("uav-utm-strict-ops")

UTM_BASE_URL = os.getenv("UTM_BASE_URL", "http://127.0.0.1:8021").rstrip("/")
UTM_SERVICE_TOKEN = os.getenv("UTM_SERVICE_TOKEN", "local-dev-token").strip()
DEFAULT_TIMEOUT_S = max(1.0, float(os.getenv("MCP_UAV_UTM_TIMEOUT_S", "6.0") or "6.0"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_success(resp: Any) -> bool:
    return isinstance(resp, dict) and str(resp.get("status", "")).strip().lower() == "success"


def _query(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    if not isinstance(params, dict) or not params:
        return path
    q = urlencode({k: v for k, v in params.items() if v is not None})
    if not q:
        return path
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}{q}"


def _http_call(
    *,
    path: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    url = f"{UTM_BASE_URL}{path if path.startswith('/') else '/' + path}"
    headers: Dict[str, str] = {"Accept": "application/json"}
    if UTM_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {UTM_SERVICE_TOKEN}"
    body: bytes | None = None
    if isinstance(payload, dict):
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = UrlRequest(url=url, data=body, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            return parsed
        return {"status": "error", "error": "non_object_response", "response": parsed}
    except HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            raw = str(e)
        try:
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                parsed.setdefault("status", "error")
                parsed.setdefault("http_status", int(getattr(e, "code", 0) or 0))
                return parsed
        except Exception:
            pass
        return {"status": "error", "error": f"http_{int(getattr(e, 'code', 0) or 0)}", "details": raw[:4000]}
    except URLError as e:
        return {"status": "error", "error": "connection_failed", "details": str(e.reason)}
    except Exception as e:
        return {"status": "error", "error": "request_failed", "details": str(e)}


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(path=_query(path, params), method="GET")


def _post(path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(path=path, method="POST", payload=payload or {})


def _put(path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(path=path, method="PUT", payload=payload or {})


def _delete(path: str) -> Dict[str, Any]:
    return _http_call(path=path, method="DELETE")


def _default_volume4d(airspace_segment: str = "sector-A3", duration_minutes: int = 30) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(minutes=max(5, int(duration_minutes)))
    return {
        "x": [0, 400],
        "y": [0, 300],
        "z": [0, 120],
        "time_start": now.isoformat().replace("+00:00", "Z"),
        "time_end": end.isoformat().replace("+00:00", "Z"),
        "bounds": {"airspace_segment": airspace_segment},
    }


@mcp.tool()
def strict_ops_overview() -> Dict[str, Any]:
    """List strict UTM operations exposed by this MCP server."""
    return {
        "status": "success",
        "server": "uav-utm-strict-ops",
        "generated_at": _now_iso(),
        "base_url": UTM_BASE_URL,
        "groups": {
            "dss": [
                "dss_state",
                "dss_upsert_participant",
                "dss_upsert_subscription",
                "dss_query_subscriptions",
                "dss_upsert_operational_intent",
                "dss_query_operational_intents",
                "dss_delete_operational_intent",
                "dss_delete_subscription",
                "dss_query_notifications",
                "dss_ack_notification",
                "dss_dispatch_notifications",
            ],
            "conformance": [
                "conformance_run_local",
                "conformance_last",
                "compliance_export",
            ],
            "security": [
                "security_status",
                "security_trust_store",
                "security_register_peer_key",
                "security_rotate_key",
                "security_upsert_service_token",
                "security_get_key_rotation_policy",
                "security_set_key_rotation_policy",
            ],
        },
    }


@mcp.tool()
def dss_state() -> Dict[str, Any]:
    """Get DSS state summary (intents/subscriptions/participants/notifications)."""
    res = _get("/api/utm/dss/state")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_upsert_participant(
    participant_id: str = "uss-local-user-1",
    uss_base_url: str = "http://127.0.0.1:9000",
    roles_csv: str = "uss",
    status: str = "active",
) -> Dict[str, Any]:
    """Create or update DSS participant."""
    roles = [r.strip() for r in str(roles_csv or "").split(",") if r.strip()]
    if not roles:
        roles = ["uss"]
    res = _post(
        "/api/utm/dss/participants",
        {
            "participant_id": participant_id,
            "uss_base_url": uss_base_url,
            "roles": roles,
            "status": status,
        },
    )
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_upsert_subscription(
    subscription_id: str = "sub-uav-a3",
    manager_uss_id: str = "uss-local-user-1",
    uss_base_url: str = "http://127.0.0.1:9000",
    callback_url: str = "local://uss-local-user-1/callback",
    notify_for_csv: str = "create,update,delete",
    airspace_segment: str = "sector-A3",
    duration_minutes: int = 60,
) -> Dict[str, Any]:
    """Create or update DSS subscription."""
    notify_for = [x.strip() for x in str(notify_for_csv or "").split(",") if x.strip()]
    if not notify_for:
        notify_for = ["create", "update", "delete"]
    res = _post(
        "/api/utm/dss/subscriptions",
        {
            "subscription_id": subscription_id,
            "manager_uss_id": manager_uss_id,
            "uss_base_url": uss_base_url,
            "callback_url": callback_url,
            "notify_for": notify_for,
            "volume4d": _default_volume4d(airspace_segment, duration_minutes=duration_minutes),
        },
    )
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_query_subscriptions(manager_uss_id: str = "") -> Dict[str, Any]:
    """Query DSS subscriptions by manager USS (optional)."""
    res = _get("/api/utm/dss/subscriptions", {"manager_uss_id": manager_uss_id or None})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_upsert_operational_intent(
    intent_id: str = "intent-uav-1-route-1",
    manager_uss_id: str = "uss-local-user-1",
    state: str = "accepted",
    priority: str = "normal",
    conflict_policy: str = "reject",
    airspace_segment: str = "sector-A3",
    duration_minutes: int = 30,
    metadata_json: str = "",
) -> Dict[str, Any]:
    """Create or update DSS operational intent."""
    metadata: Dict[str, Any] = {"source": "mcp_strict_ops"}
    if isinstance(metadata_json, str) and metadata_json.strip():
        try:
            parsed = json.loads(metadata_json)
            if isinstance(parsed, dict):
                metadata.update(parsed)
        except Exception:
            return {"status": "error", "error": "invalid_metadata_json"}
    res = _post(
        "/api/utm/dss/operational-intents",
        {
            "intent_id": intent_id,
            "manager_uss_id": manager_uss_id,
            "state": state,
            "priority": priority,
            "conflict_policy": conflict_policy,
            "volume4d": _default_volume4d(airspace_segment, duration_minutes=duration_minutes),
            "metadata": metadata,
        },
    )
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_query_operational_intents(manager_uss_id: str = "", states_csv: str = "") -> Dict[str, Any]:
    """Query DSS operational intents by manager USS and/or states list."""
    params: Dict[str, Any] = {"manager_uss_id": manager_uss_id or None}
    states = [s.strip() for s in str(states_csv or "").split(",") if s.strip()]
    if states:
        params["states"] = ",".join(states)
    res = _get("/api/utm/dss/operational-intents/query", params)
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_delete_operational_intent(intent_id: str) -> Dict[str, Any]:
    """Delete DSS operational intent by ID."""
    res = _delete(f"/api/utm/dss/operational-intents/{intent_id}")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_delete_subscription(subscription_id: str) -> Dict[str, Any]:
    """Delete DSS subscription by ID."""
    res = _delete(f"/api/utm/dss/subscriptions/{subscription_id}")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_query_notifications(limit: int = 100, status: str = "", subscription_id: str = "") -> Dict[str, Any]:
    """Query DSS notifications with optional filters."""
    res = _get(
        "/api/utm/dss/notifications",
        {
            "limit": max(1, min(1000, int(limit))),
            "status": status or None,
            "subscription_id": subscription_id or None,
        },
    )
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_ack_notification(notification_id: str) -> Dict[str, Any]:
    """Acknowledge DSS notification by ID."""
    res = _post(f"/api/utm/dss/notifications/{notification_id}/ack", {})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def dss_dispatch_notifications(run_limit: int = 1) -> Dict[str, Any]:
    """Run DSS notification dispatch loop."""
    res = _post("/api/utm/dss/notifications/dispatch", {"run_limit": max(1, min(50, int(run_limit)))})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def conformance_run_local(reset_before_run: bool = True) -> Dict[str, Any]:
    """Run local DSS conformance checks."""
    res = _post("/api/utm/conformance/run-local", {"reset_before_run": bool(reset_before_run)})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def conformance_last() -> Dict[str, Any]:
    """Return latest conformance run summary."""
    res = _get("/api/utm/conformance/last")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def compliance_export(include_rtm_text: bool = False) -> Dict[str, Any]:
    """Export compliance evidence summary."""
    res = _get("/api/utm/compliance/export", {"include_rtm_text": str(bool(include_rtm_text)).lower()})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_status() -> Dict[str, Any]:
    """Get UTM security state summary."""
    res = _get("/api/utm/security/status")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_trust_store() -> Dict[str, Any]:
    """Get UTM trust-store entries."""
    res = _get("/api/utm/security/trust-store")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_register_peer_key(
    issuer: str = "peer-uss-a",
    key_id: str = "key-1",
    secret: str = "demo-secret",
    status: str = "active",
) -> Dict[str, Any]:
    """Register/update trusted peer signing key."""
    res = _post(
        "/api/utm/security/trust-store/peers",
        {"issuer": issuer, "key_id": key_id, "secret": secret, "status": status},
    )
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_rotate_key(issuer: str = "utm-local") -> Dict[str, Any]:
    """Rotate UTM signing key for an issuer."""
    res = _post("/api/utm/security/keys/rotate", {"issuer": issuer})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_upsert_service_token(token: str, roles_csv: str = "read,utm_write") -> Dict[str, Any]:
    """Create or update service token roles."""
    roles = [r.strip() for r in str(roles_csv or "").split(",") if r.strip()]
    if not roles:
        roles = ["read"]
    res = _post("/api/utm/security/service-tokens", {"token": token, "roles": roles})
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_get_key_rotation_policy() -> Dict[str, Any]:
    """Get key rotation policy."""
    res = _get("/api/utm/security/key-rotation-policy")
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


@mcp.tool()
def security_set_key_rotation_policy(
    max_age_days: int = 30,
    overlap_days: int = 7,
    auto_rotate: bool = False,
) -> Dict[str, Any]:
    """Set key rotation policy."""
    res = _put(
        "/api/utm/security/key-rotation-policy",
        {
            "max_age_days": max(1, int(max_age_days)),
            "overlap_days": max(0, int(overlap_days)),
            "auto_rotate": bool(auto_rotate),
        },
    )
    return {"status": "success" if _is_success(res) else "error", "generated_at": _now_iso(), "result": res}


if __name__ == "__main__":
    logger.info("Starting MCP UAV/UTM strict-ops server on stdio")
    mcp.run()
