#!/usr/bin/env python3
"""
mcp_uav_utm_procedures.py (STDIO MCP server)

Procedure-first MCP tools for UAV + UTM operations.

This server wraps existing local APIs:
- UAV API: http://127.0.0.1:8020
- UTM API: http://127.0.0.1:8021
- Network API (optional snapshot): http://127.0.0.1:8022

Environment:
  UAV_BASE_URL=http://127.0.0.1:8020
  UTM_BASE_URL=http://127.0.0.1:8021
  NETWORK_BASE_URL=http://127.0.0.1:8022
  UTM_SERVICE_TOKEN=local-dev-token
  MCP_UAV_UTM_TIMEOUT_S=6.0
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

try:
    from mcp.server.fastmcp import FastMCP
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "mcp_uav_utm_procedures.py requires the `mcp` package. "
        "Install it in the active environment before running this server."
    ) from e


logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp-uav-utm-procedures")

mcp = FastMCP("uav-utm-procedures")

UAV_BASE_URL = os.getenv("UAV_BASE_URL", "http://127.0.0.1:8020").rstrip("/")
UTM_BASE_URL = os.getenv("UTM_BASE_URL", "http://127.0.0.1:8021").rstrip("/")
NETWORK_BASE_URL = os.getenv("NETWORK_BASE_URL", "http://127.0.0.1:8022").rstrip("/")
UTM_SERVICE_TOKEN = os.getenv("UTM_SERVICE_TOKEN", "local-dev-token").strip()
DEFAULT_TIMEOUT_S = max(1.0, float(os.getenv("MCP_UAV_UTM_TIMEOUT_S", "6.0") or "6.0"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_waypoints() -> List[Dict[str, Any]]:
    return [
        {"x": 10.0, "y": 10.0, "z": 20.0, "action": "TAKEOFF"},
        {"x": 60.0, "y": 70.0, "z": 35.0},
        {"x": 130.0, "y": 120.0, "z": 40.0},
        {"x": 10.0, "y": 10.0, "z": 20.0, "action": "LAND"},
    ]


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
    base_url: str,
    path: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    auth: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    url = f"{base_url}{path if path.startswith('/') else '/' + path}"
    headers: Dict[str, str] = {"Accept": "application/json"}
    body: bytes | None = None

    if isinstance(payload, dict):
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    if auth and UTM_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {UTM_SERVICE_TOKEN}"

    req = UrlRequest(url=url, data=body, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            status_code = int(getattr(resp, "status", 200))
            raw = resp.read().decode("utf-8", errors="replace")
        parsed: Any
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"status": "error", "error": "invalid_json_response", "raw": raw[:4000]}
        if isinstance(parsed, dict):
            return parsed
        return {"status": "error", "error": "non_object_response", "http_status": status_code, "response": parsed}
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


def _uav_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(base_url=UAV_BASE_URL, path=_query(path, params), method="GET", auth=False)


def _uav_post(path: str, payload: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(base_url=UAV_BASE_URL, path=_query(path, params), method="POST", payload=payload or {}, auth=False)


def _utm_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(base_url=UTM_BASE_URL, path=_query(path, params), method="GET", auth=True)


def _utm_post(path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(base_url=UTM_BASE_URL, path=path, method="POST", payload=payload or {}, auth=True)


def _net_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _http_call(base_url=NETWORK_BASE_URL, path=_query(path, params), method="GET", auth=False)


def _prepare_operator_and_assign_impl(
    *,
    user_id: str,
    uav_id: str,
    operator_license_id: str,
    license_class: str,
    uav_size_class: str,
    expires_at: str,
    active: bool,
    airspace_segment: str,
    requested_speed_mps: float,
    planned_start_at: str,
    planned_end_at: str,
) -> Dict[str, Any]:
    license_res = _utm_post(
        "/api/utm/license",
        {
            "operator_license_id": operator_license_id,
            "license_class": license_class,
            "uav_size_class": uav_size_class,
            "expires_at": expires_at,
            "active": bool(active),
        },
    )
    assign_res = _uav_post(
        "/api/uav/registry/assign",
        {
            "user_id": user_id,
            "uav_id": uav_id,
            "operator_license_id": operator_license_id,
        },
    )
    defaults_res = _uav_post(
        "/api/uav/mission-defaults",
        {
            "user_id": user_id,
            "uav_id": uav_id,
            "airspace_segment": airspace_segment,
            "requested_speed_mps": float(requested_speed_mps),
            "planned_start_at": planned_start_at or None,
            "planned_end_at": planned_end_at or None,
        },
    )
    return {
        "status": "success" if (_is_success(license_res) and _is_success(assign_res) and _is_success(defaults_res)) else "error",
        "steps": {
            "register_license": license_res,
            "assign_uav": assign_res,
            "set_mission_defaults": defaults_res,
        },
    }


def _plan_route_impl(*, user_id: str, uav_id: str, route_id: str, waypoints: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    return _uav_post(
        "/api/uav/sim/plan",
        {
            "user_id": user_id,
            "uav_id": uav_id,
            "route_id": route_id,
            "waypoints": waypoints if isinstance(waypoints, list) and waypoints else _default_waypoints(),
        },
    )


def _submit_mission_impl(
    *,
    user_id: str,
    uav_id: str,
    airspace_segment: str,
    operator_license_id: str,
    required_license_class: str,
    requested_speed_mps: float,
    dss_conflict_policy: str,
    planned_start_at: str,
    planned_end_at: str,
) -> Dict[str, Any]:
    return _uav_post(
        "/api/uav/sim/utm-submit-mission",
        {
            "user_id": user_id,
            "uav_id": uav_id,
            "airspace_segment": airspace_segment,
            "operator_license_id": operator_license_id,
            "required_license_class": required_license_class,
            "requested_speed_mps": float(requested_speed_mps),
            "dss_conflict_policy": dss_conflict_policy,
            "planned_start_at": planned_start_at or None,
            "planned_end_at": planned_end_at or None,
        },
    )


def _launch_and_step_impl(*, user_id: str, uav_id: str, ticks: int) -> Dict[str, Any]:
    launch_res = _uav_post("/api/uav/sim/launch", payload={}, params={"uav_id": uav_id, "user_id": user_id})
    step_res = _uav_post("/api/uav/sim/step", {"user_id": user_id, "uav_id": uav_id, "ticks": max(1, int(ticks))})
    return {
        "status": "success" if (_is_success(launch_res) and _is_success(step_res)) else "error",
        "steps": {"launch": launch_res, "step": step_res},
    }


@mcp.tool()
def tool_overview() -> Dict[str, Any]:
    """List procedure-focused UAV/UTM MCP tools and expected workflow."""
    return {
        "status": "success",
        "server": "uav-utm-procedures",
        "generated_at": _now_iso(),
        "base_urls": {
            "uav": UAV_BASE_URL,
            "utm": UTM_BASE_URL,
            "network": NETWORK_BASE_URL,
        },
        "tools": [
            "health_check",
            "status_snapshot",
            "prepare_operator_and_assign_uav",
            "plan_route",
            "submit_mission_auto",
            "launch_and_step",
            "control_action",
            "replan_and_resubmit",
            "setup_dss_subscription",
            "run_standard_procedure",
        ],
        "recommended_order": [
            "prepare_operator_and_assign_uav",
            "plan_route",
            "submit_mission_auto",
            "launch_and_step",
        ],
        "notes": [
            "UTM API calls include bearer auth using UTM_SERVICE_TOKEN.",
            "All tools return raw backend step payloads for traceability.",
        ],
    }


@mcp.tool()
def health_check(airspace_segment: str = "sector-A3", uav_id: str = "uav-1") -> Dict[str, Any]:
    """Check connectivity to UAV/UTM/Network services used by this MCP server."""
    uav = _uav_get("/api/uav/sim/fleet")
    utm = _utm_get("/api/utm/sync")
    net = _net_get("/api/network/mission/state", {"airspace_segment": airspace_segment, "selected_uav_id": uav_id})
    return {
        "status": "success" if (_is_success(uav) and _is_success(utm) and _is_success(net)) else "error",
        "generated_at": _now_iso(),
        "checks": {
            "uav_api": {"ok": _is_success(uav), "response": uav},
            "utm_api": {"ok": _is_success(utm), "response": utm},
            "network_api": {"ok": _is_success(net), "response": net},
        },
    }


@mcp.tool()
def status_snapshot(
    uav_id: str = "uav-1",
    user_id: str = "user-1",
    airspace_segment: str = "sector-A3",
    operator_license_id: str = "op-001",
    include_network: bool = True,
) -> Dict[str, Any]:
    """Return combined UAV, UTM, and optional Network status snapshot for one UAV mission."""
    uav_state = _uav_get("/api/uav/sim/state", {"uav_id": uav_id, "user_id": user_id, "operator_license_id": operator_license_id})
    utm_state = _utm_get("/api/utm/state", {"airspace_segment": airspace_segment, "operator_license_id": operator_license_id})
    net_state = (
        _net_get("/api/network/mission/state", {"airspace_segment": airspace_segment, "selected_uav_id": uav_id})
        if include_network
        else {"status": "skipped", "reason": "include_network=false"}
    )
    overall_ok = _is_success(uav_state) and _is_success(utm_state) and (_is_success(net_state) or str(net_state.get("status")) == "skipped")
    return {
        "status": "success" if overall_ok else "error",
        "generated_at": _now_iso(),
        "result": {
            "uav": uav_state,
            "utm": utm_state,
            "network": net_state,
        },
    }


@mcp.tool()
def prepare_operator_and_assign_uav(
    user_id: str = "user-1",
    uav_id: str = "uav-1",
    operator_license_id: str = "op-001",
    license_class: str = "VLOS",
    uav_size_class: str = "middle",
    expires_at: str = "2099-01-01T00:00:00Z",
    active: bool = True,
    airspace_segment: str = "sector-A3",
    requested_speed_mps: float = 12.0,
    planned_start_at: str = "",
    planned_end_at: str = "",
) -> Dict[str, Any]:
    """Create/update operator license in UTM, assign UAV to user, and store mission defaults."""
    out = _prepare_operator_and_assign_impl(
        user_id=user_id,
        uav_id=uav_id,
        operator_license_id=operator_license_id,
        license_class=license_class,
        uav_size_class=uav_size_class,
        expires_at=expires_at,
        active=active,
        airspace_segment=airspace_segment,
        requested_speed_mps=requested_speed_mps,
        planned_start_at=planned_start_at,
        planned_end_at=planned_end_at,
    )
    out["generated_at"] = _now_iso()
    return out


@mcp.tool()
def plan_route(
    user_id: str = "user-1",
    uav_id: str = "uav-1",
    route_id: str = "route-1",
    waypoints: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Plan or replace the UAV route in simulator mode."""
    result = _plan_route_impl(user_id=user_id, uav_id=uav_id, route_id=route_id, waypoints=waypoints)
    return {"status": "success" if _is_success(result) else "error", "generated_at": _now_iso(), "result": result}


@mcp.tool()
def submit_mission_auto(
    user_id: str = "user-1",
    uav_id: str = "uav-1",
    airspace_segment: str = "sector-A3",
    operator_license_id: str = "op-001",
    required_license_class: str = "VLOS",
    requested_speed_mps: float = 12.0,
    dss_conflict_policy: str = "reject",
    planned_start_at: str = "",
    planned_end_at: str = "",
) -> Dict[str, Any]:
    """Run backend auto preflight workflow: route checks + geofence + verify + approval + DSS integration."""
    result = _submit_mission_impl(
        user_id=user_id,
        uav_id=uav_id,
        airspace_segment=airspace_segment,
        operator_license_id=operator_license_id,
        required_license_class=required_license_class,
        requested_speed_mps=requested_speed_mps,
        dss_conflict_policy=dss_conflict_policy,
        planned_start_at=planned_start_at,
        planned_end_at=planned_end_at,
    )
    approved = bool(((result.get("result") or {}).get("approved"))) if isinstance(result, dict) else False
    return {
        "status": "success" if (_is_success(result) and approved) else "error",
        "generated_at": _now_iso(),
        "approved": approved,
        "result": result,
    }


@mcp.tool()
def launch_and_step(user_id: str = "user-1", uav_id: str = "uav-1", ticks: int = 3) -> Dict[str, Any]:
    """Launch UAV mission and advance it by N ticks."""
    out = _launch_and_step_impl(user_id=user_id, uav_id=uav_id, ticks=ticks)
    out["generated_at"] = _now_iso()
    return out


@mcp.tool()
def control_action(
    action: str = "hold",
    user_id: str = "user-1",
    uav_id: str = "uav-1",
    reason: str = "operator_request",
) -> Dict[str, Any]:
    """Execute one control command: hold, resume, rth, or land."""
    op = str(action or "").strip().lower()
    if op == "hold":
        res = _uav_post("/api/uav/sim/hold", {"user_id": user_id, "uav_id": uav_id, "reason": reason})
    elif op == "resume":
        res = _uav_post("/api/uav/sim/resume", payload={}, params={"uav_id": uav_id, "user_id": user_id})
    elif op in {"rth", "return_to_home"}:
        res = _uav_post("/api/uav/sim/rth", payload={}, params={"uav_id": uav_id, "user_id": user_id})
    elif op == "land":
        res = _uav_post("/api/uav/sim/land", payload={}, params={"uav_id": uav_id, "user_id": user_id})
    else:
        return {
            "status": "error",
            "error": "unsupported_action",
            "allowed_actions": ["hold", "resume", "rth", "land"],
        }
    return {
        "status": "success" if _is_success(res) else "error",
        "generated_at": _now_iso(),
        "action": op,
        "result": res,
    }


@mcp.tool()
def replan_and_resubmit(
    user_id: str = "user-1",
    uav_id: str = "uav-1",
    airspace_segment: str = "sector-A3",
    user_request: str = "avoid no-fly zone conflict",
    optimization_profile: str = "balanced",
    operator_license_id: str = "op-001",
    required_license_class: str = "VLOS",
    requested_speed_mps: float = 12.0,
    dss_conflict_policy: str = "reject",
    auto_utm_verify: bool = True,
    route_category: str = "agent_replanned",
    replan_context: str = "general",
) -> Dict[str, Any]:
    """Replan route via UTM NFZ logic and then re-run submit mission workflow."""
    replan = _uav_post(
        "/api/uav/sim/replan-via-utm-nfz",
        {
            "user_id": user_id,
            "uav_id": uav_id,
            "airspace_segment": airspace_segment,
            "user_request": user_request,
            "optimization_profile": optimization_profile,
            "operator_license_id": operator_license_id,
            "auto_utm_verify": bool(auto_utm_verify),
            "route_category": route_category,
            "replan_context": replan_context,
        },
    )
    submit = _submit_mission_impl(
        user_id=user_id,
        uav_id=uav_id,
        airspace_segment=airspace_segment,
        operator_license_id=operator_license_id,
        required_license_class=required_license_class,
        requested_speed_mps=requested_speed_mps,
        dss_conflict_policy=dss_conflict_policy,
        planned_start_at="",
        planned_end_at="",
    )
    approved = bool(((submit.get("result") or {}).get("approved"))) if isinstance(submit, dict) else False
    return {
        "status": "success" if (_is_success(replan) and _is_success(submit) and approved) else "error",
        "generated_at": _now_iso(),
        "approved": approved,
        "steps": {
            "replan": replan,
            "submit_mission_auto": submit,
        },
    }


@mcp.tool()
def setup_dss_subscription(
    participant_id: str = "uss-local-user-1",
    uss_base_url: str = "http://127.0.0.1:9000",
    callback_url: str = "local://uss-local-user-1/callback",
    airspace_segment: str = "sector-A3",
    subscription_id: str = "sub-uav-a3",
    duration_minutes: int = 60,
) -> Dict[str, Any]:
    """Create/update DSS participant and broad subscription for airspace monitoring."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(minutes=max(5, int(duration_minutes)))
    participant = _utm_post(
        "/api/utm/dss/participants",
        {
            "participant_id": participant_id,
            "uss_base_url": uss_base_url,
            "roles": ["uss"],
            "status": "active",
        },
    )
    subscription = _utm_post(
        "/api/utm/dss/subscriptions",
        {
            "subscription_id": subscription_id,
            "manager_uss_id": participant_id,
            "uss_base_url": uss_base_url,
            "callback_url": callback_url,
            "notify_for": ["create", "update", "delete"],
            "volume4d": {
                "x": [0, 400],
                "y": [0, 300],
                "z": [0, 120],
                "time_start": now.isoformat().replace("+00:00", "Z"),
                "time_end": end.isoformat().replace("+00:00", "Z"),
                "bounds": {"airspace_segment": airspace_segment},
            },
        },
    )
    return {
        "status": "success" if (_is_success(participant) and _is_success(subscription)) else "error",
        "generated_at": _now_iso(),
        "result": {
            "participant": participant,
            "subscription": subscription,
        },
    }


@mcp.tool()
def run_standard_procedure(
    user_id: str = "user-1",
    uav_id: str = "uav-1",
    route_id: str = "route-1",
    operator_license_id: str = "op-001",
    license_class: str = "VLOS",
    airspace_segment: str = "sector-A3",
    requested_speed_mps: float = 12.0,
    ticks: int = 3,
    waypoints: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One-shot happy-path procedure: prepare -> plan -> submit -> launch -> step."""
    prepare = _prepare_operator_and_assign_impl(
        user_id=user_id,
        uav_id=uav_id,
        operator_license_id=operator_license_id,
        license_class=license_class,
        uav_size_class="middle",
        expires_at="2099-01-01T00:00:00Z",
        active=True,
        airspace_segment=airspace_segment,
        requested_speed_mps=requested_speed_mps,
        planned_start_at="",
        planned_end_at="",
    )
    plan = _plan_route_impl(user_id=user_id, uav_id=uav_id, route_id=route_id, waypoints=waypoints)
    submit = _submit_mission_impl(
        user_id=user_id,
        uav_id=uav_id,
        airspace_segment=airspace_segment,
        operator_license_id=operator_license_id,
        required_license_class=license_class,
        requested_speed_mps=requested_speed_mps,
        dss_conflict_policy="reject",
        planned_start_at="",
        planned_end_at="",
    )
    approved = bool(((submit.get("result") or {}).get("approved"))) if isinstance(submit, dict) else False
    launch_step = _launch_and_step_impl(user_id=user_id, uav_id=uav_id, ticks=ticks) if approved else {
        "status": "skipped",
        "reason": "mission_not_approved",
    }
    ok = _is_success(prepare) and _is_success(plan) and _is_success(submit) and approved and _is_success(launch_step)
    return {
        "status": "success" if ok else "error",
        "generated_at": _now_iso(),
        "summary": {
            "approved": approved,
            "launched": _is_success(launch_step),
        },
        "steps": {
            "prepare_operator_and_assign_uav": prepare,
            "plan_route": plan,
            "submit_mission_auto": submit,
            "launch_and_step": launch_step,
        },
    }


if __name__ == "__main__":
    logger.info("Starting MCP UAV/UTM procedure server on stdio")
    mcp.run()
