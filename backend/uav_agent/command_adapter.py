from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .simulator import SIM

SUPPORTED_OPERATIONS = {"launch", "step", "hold", "resume", "rth", "land"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_true(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_operation(operation: str) -> str:
    op = str(operation or "").strip().lower()
    aliases = {
        "return_to_home": "rth",
        "sim_step": "step",
    }
    return aliases.get(op, op)


def _resolve_http_path_template() -> str:
    raw = str(os.getenv("UAV_CONTROL_HTTP_PATH_TEMPLATE", "/api/uav/control/{op}") or "").strip()
    if not raw:
        return "/api/uav/control/{op}"
    return raw if raw.startswith("/") else f"/{raw}"


def _resolve_adapter_mode(*, uav_id: str, requested_mode: str | None = None) -> str:
    mode = str(requested_mode if requested_mode is not None else os.getenv("UAV_CONTROL_ADAPTER_MODE", "sim")).strip().lower()
    if mode not in {"sim", "internal", "http", "mavlink", "dji", "auto"}:
        mode = "sim"
    if mode in {"internal"}:
        return "sim"
    if mode in {"mavlink", "dji"}:
        return "http"
    if mode != "auto":
        return mode
    snap = SIM.status_if_exists(uav_id) or {}
    source = str(snap.get("data_source", "simulated") or "simulated").strip().lower()
    if source not in {"", "simulated"} and str(os.getenv("UAV_CONTROL_HTTP_BASE_URL", "")).strip():
        return "http"
    return "sim"


def _mirror_mode() -> str:
    raw = str(os.getenv("UAV_CONTROL_MIRROR_MODE", "optimistic") or "optimistic").strip().lower()
    return raw if raw in {"optimistic", "telemetry_only", "none"} else "optimistic"


def _fallback_to_sim_enabled() -> bool:
    return _is_true(os.getenv("UAV_CONTROL_ADAPTER_FALLBACK_TO_SIM", "1"), default=True)


def _run_sim_operation(operation: str, *, uav_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    op = _normalize_operation(operation)
    if op == "launch":
        return SIM.launch(uav_id)
    if op == "step":
        return SIM.step(uav_id, ticks=int(params.get("ticks", 1)))
    if op == "hold":
        return SIM.hold(uav_id, str(params.get("reason", "operator_request")))
    if op == "resume":
        return SIM.resume(uav_id)
    if op == "rth":
        return SIM.rth(uav_id)
    if op == "land":
        return SIM.land(uav_id)
    raise ValueError(f"Unsupported UAV control operation: {op}")


def _extract_telemetry_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("telemetry"), dict):
            return dict(payload["telemetry"])
        if isinstance(payload.get("uav"), dict):
            return dict(payload["uav"])
        if isinstance(payload.get("result"), dict):
            nested = payload["result"]
            if isinstance(nested.get("telemetry"), dict):
                return dict(nested["telemetry"])
            if isinstance(nested.get("uav"), dict):
                return dict(nested["uav"])
            if {"position", "armed", "active"}.intersection(set(nested.keys())):
                return dict(nested)
        if {"position", "armed", "active"}.intersection(set(payload.keys())):
            return dict(payload)
    return {}


def _ingest_adapter_telemetry(
    *,
    uav_id: str,
    operation: str,
    adapter_name: str,
    adapter_payload: Dict[str, Any],
) -> Dict[str, Any] | None:
    telemetry = _extract_telemetry_payload(adapter_payload)
    if not telemetry:
        return None
    source = str(telemetry.get("source", f"{adapter_name}_adapter") or f"{adapter_name}_adapter")
    source_meta = {
        "adapter": adapter_name,
        "operation": operation,
        "ingested_at": _utc_now(),
    }
    if isinstance(adapter_payload.get("meta"), dict):
        source_meta["adapter_meta"] = dict(adapter_payload.get("meta"))
    return SIM.ingest_live_state(
        uav_id,
        route_id=telemetry.get("route_id"),
        waypoints=telemetry.get("waypoints") if isinstance(telemetry.get("waypoints"), list) else None,
        position=telemetry.get("position") if isinstance(telemetry.get("position"), dict) else None,
        waypoint_index=telemetry.get("waypoint_index"),
        velocity_mps=telemetry.get("velocity_mps"),
        battery_pct=telemetry.get("battery_pct"),
        flight_phase=telemetry.get("flight_phase"),
        armed=telemetry.get("armed"),
        active=telemetry.get("active"),
        source=source,
        source_meta=source_meta,
    )


def _http_adapter_execute(operation: str, *, uav_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(os.getenv("UAV_CONTROL_HTTP_BASE_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return {
            "status": "error",
            "error": "http_adapter_not_configured",
            "details": "Set UAV_CONTROL_HTTP_BASE_URL to enable HTTP command uplink.",
        }
    path_template = _resolve_http_path_template()
    path = path_template.format(op=_normalize_operation(operation), uav_id=uav_id)
    url = urljoin(f"{base_url}/", path.lstrip("/"))
    timeout_s = float(str(os.getenv("UAV_CONTROL_HTTP_TIMEOUT_S", "2.0") or "2.0").strip() or "2.0")
    headers = {"Content-Type": "application/json"}
    token = str(os.getenv("UAV_CONTROL_HTTP_AUTH_TOKEN", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req_body = {
        "uav_id": uav_id,
        "operation": _normalize_operation(operation),
        "params": dict(params),
        "requested_at": _utc_now(),
    }
    req = Request(
        url=url,
        data=json.dumps(req_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=max(0.1, timeout_s)) as resp:
            body_raw = resp.read().decode("utf-8")
            status_code = int(getattr(resp, "status", 200))
    except HTTPError as exc:
        try:
            body_raw = exc.read().decode("utf-8")
        except Exception:
            body_raw = ""
        return {
            "status": "error",
            "error": "http_adapter_http_error",
            "details": str(exc),
            "status_code": int(getattr(exc, "code", 500) or 500),
            "response_raw": body_raw[:2048],
        }
    except URLError as exc:
        return {
            "status": "error",
            "error": "http_adapter_unreachable",
            "details": str(exc.reason if hasattr(exc, "reason") else exc),
            "url": url,
        }
    except Exception as exc:
        return {"status": "error", "error": "http_adapter_exception", "details": str(exc), "url": url}

    parsed: Dict[str, Any] = {}
    if body_raw.strip():
        try:
            candidate = json.loads(body_raw)
            parsed = candidate if isinstance(candidate, dict) else {"raw": candidate}
        except Exception:
            parsed = {"raw": body_raw}
    status_token = str(parsed.get("status", "")).strip().lower()
    ok = 200 <= status_code < 300 and status_token not in {"error", "failed", "failure"}
    return {
        "status": "success" if ok else "error",
        "status_code": status_code,
        "payload": parsed,
        "url": url,
    }


def execute_uav_control(
    operation: str,
    *,
    uav_id: str,
    params: Dict[str, Any] | None = None,
    requested_mode: str | None = None,
) -> Dict[str, Any]:
    op = _normalize_operation(operation)
    if op not in SUPPORTED_OPERATIONS:
        return {"status": "error", "error": f"unsupported_operation:{op}"}
    args = dict(params or {})

    mode = _resolve_adapter_mode(uav_id=uav_id, requested_mode=requested_mode)
    mirror_mode = _mirror_mode()
    fallback_to_sim = _fallback_to_sim_enabled()

    adapter_result: Dict[str, Any] | None = None
    state: Dict[str, Any] | None = None
    used_mode = mode
    fallback_used = False

    if mode == "sim":
        try:
            state = _run_sim_operation(op, uav_id=uav_id, params=args)
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "control_adapter": {
                    "requested_mode": requested_mode or os.getenv("UAV_CONTROL_ADAPTER_MODE", "sim"),
                    "resolved_mode": "sim",
                    "fallback_to_sim": fallback_to_sim,
                },
            }
    else:
        adapter_result = _http_adapter_execute(op, uav_id=uav_id, params=args)
        if adapter_result.get("status") == "success":
            payload = adapter_result.get("payload") if isinstance(adapter_result.get("payload"), dict) else {}
            ingested = _ingest_adapter_telemetry(
                uav_id=uav_id,
                operation=op,
                adapter_name=mode,
                adapter_payload=payload if isinstance(payload, dict) else {},
            )
            if isinstance(ingested, dict):
                state = ingested
            elif mirror_mode == "optimistic":
                try:
                    state = _run_sim_operation(op, uav_id=uav_id, params=args)
                except Exception as exc:
                    state = SIM.status(uav_id)
                    if isinstance(adapter_result, dict):
                        adapter_result["mirror_error"] = str(exc)
            else:
                state = SIM.status(uav_id)
        elif fallback_to_sim:
            try:
                state = _run_sim_operation(op, uav_id=uav_id, params=args)
                used_mode = "sim"
                fallback_used = True
            except Exception as exc:
                return {
                    "status": "error",
                    "error": str(exc),
                    "adapter_result": adapter_result,
                    "control_adapter": {
                        "requested_mode": requested_mode or os.getenv("UAV_CONTROL_ADAPTER_MODE", "sim"),
                        "resolved_mode": mode,
                        "fallback_to_sim": fallback_to_sim,
                        "fallback_used": False,
                        "mirror_mode": mirror_mode,
                    },
                }
        else:
            return {
                "status": "error",
                "error": str(adapter_result.get("error") or "adapter_command_failed"),
                "adapter_result": adapter_result,
                "control_adapter": {
                    "requested_mode": requested_mode or os.getenv("UAV_CONTROL_ADAPTER_MODE", "sim"),
                    "resolved_mode": mode,
                    "fallback_to_sim": fallback_to_sim,
                    "fallback_used": False,
                    "mirror_mode": mirror_mode,
                },
            }

    return {
        "status": "success",
        "result": state if isinstance(state, dict) else SIM.status(uav_id),
        "adapter_result": adapter_result,
        "control_adapter": {
            "requested_mode": requested_mode or os.getenv("UAV_CONTROL_ADAPTER_MODE", "sim"),
            "resolved_mode": mode,
            "used_mode": used_mode,
            "fallback_to_sim": fallback_to_sim,
            "fallback_used": fallback_used,
            "mirror_mode": mirror_mode,
        },
    }


def get_uav_control_adapter_status(*, uav_id: str = "uav-1") -> Dict[str, Any]:
    requested_mode = str(os.getenv("UAV_CONTROL_ADAPTER_MODE", "sim") or "sim").strip().lower()
    resolved_mode = _resolve_adapter_mode(uav_id=uav_id, requested_mode=requested_mode)
    snap = SIM.status_if_exists(uav_id) or {}
    data_source = str(snap.get("data_source", "absent") or "absent")
    path_template = _resolve_http_path_template()
    base_url = str(os.getenv("UAV_CONTROL_HTTP_BASE_URL", "") or "").strip().rstrip("/")
    return {
        "uav_id": uav_id,
        "supported_operations": sorted(SUPPORTED_OPERATIONS),
        "requested_mode": requested_mode,
        "resolved_mode": resolved_mode,
        "fallback_to_sim": _fallback_to_sim_enabled(),
        "mirror_mode": _mirror_mode(),
        "http": {
            "configured": bool(base_url),
            "base_url": base_url,
            "path_template": path_template,
            "timeout_s": float(str(os.getenv("UAV_CONTROL_HTTP_TIMEOUT_S", "2.0") or "2.0").strip() or "2.0"),
            "auth_configured": bool(str(os.getenv("UAV_CONTROL_HTTP_AUTH_TOKEN", "") or "").strip()),
        },
        "uav_data_source": data_source,
    }


def parse_control_operation(payload_op: str) -> Tuple[bool, str]:
    op = _normalize_operation(payload_op)
    return (op in SUPPORTED_OPERATIONS, op)


__all__ = [
    "SUPPORTED_OPERATIONS",
    "execute_uav_control",
    "get_uav_control_adapter_status",
    "parse_control_operation",
]
