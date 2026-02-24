"""UAV + UTM simulator API for frontend controls."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except Exception as e:  # pragma: no cover
    raise RuntimeError("uav_agent.api requires fastapi and pydantic") from e

try:  # pragma: no cover - optional dependency at runtime
    from langchain_ollama import ChatOllama
except Exception:  # pragma: no cover
    ChatOllama = None  # type: ignore[assignment]

from oran_agent.config.settings import MODEL, OLLAMA_URL
from .graph import run_copilot_workflow
from .simulator import DEFAULT_ROUTE, SIM
from .tools import (
    uav_hold,
    uav_land,
    uav_launch,
    uav_plan_route,
    uav_replan_route_via_utm_nfz,
    uav_request_utm_approval,
    uav_resume,
    uav_return_to_home,
    uav_sim_step,
    uav_status,
    uav_submit_route_to_utm_geofence_check,
)
from network_agent.service import NETWORK_MISSION_SERVICE
from utm_agent.service import UTM_SERVICE
from agent_db import AgentDB


class WaypointModel(BaseModel):
    x: float
    y: float
    z: float
    action: Optional[str] = None


class PlanRoutePayload(BaseModel):
    uav_id: str = "uav-1"
    route_id: str = "route-1"
    waypoints: List[WaypointModel] = Field(default_factory=lambda: [WaypointModel(**wp) for wp in DEFAULT_ROUTE])


class ApprovalPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"
    requested_speed_mps: float = 12.0
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class WeatherPayload(BaseModel):
    airspace_segment: str = "sector-A3"
    wind_mps: float = 8.0
    visibility_km: float = 10.0
    precip_mmph: float = 0.0
    storm_alert: bool = False


class StepPayload(BaseModel):
    uav_id: str = "uav-1"
    ticks: int = 1


class HoldPayload(BaseModel):
    uav_id: str = "uav-1"
    reason: str = "operator_request"


class ReplanPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    user_request: str = "avoid nfz on north side"
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointModel]] = None
    optimization_profile: str = "balanced"


class UavAgentChatPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    prompt: str = "Optimize path considering NFZ and network coverage"
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointModel]] = None
    optimization_profile: str = "balanced"
    network_mode: Optional[str] = None  # coverage | qos | power
    auto_verify: bool = True
    auto_network_optimize: bool = True


class LicensePayload(BaseModel):
    operator_license_id: str
    license_class: str = "VLOS"
    expires_at: str = "2099-01-01T00:00:00Z"
    active: bool = True


class NoFlyZonePayload(BaseModel):
    zone_id: Optional[str] = None
    cx: float
    cy: float
    radius_m: float = 30.0
    z_min: float = 0.0
    z_max: float = 120.0
    reason: str = "operator_defined"


class CorridorPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"


class RouteCheckPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    requested_speed_mps: float = 12.0
    route_id: Optional[str] = None


class TimeWindowCheckPayload(BaseModel):
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class LicenseCheckPayload(BaseModel):
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"


class VerifyFromUavPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"
    requested_speed_mps: float = 12.0
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class NetworkStateQuery(BaseModel):
    airspace_segment: str = "sector-A3"
    selected_uav_id: Optional[str] = None


class NetworkTickPayload(BaseModel):
    steps: int = 1


class NetworkOptimizePayload(BaseModel):
    mode: str = "coverage"  # coverage | power | qos
    coverage_target_pct: float = 96.0
    max_tx_cap_dbm: float = 41.0
    qos_priority_weight: float = 68.0


class NetworkBaseStationUpdatePayload(BaseModel):
    bs_id: str
    txPowerDbm: Optional[float] = None
    tiltDeg: Optional[float] = None
    loadPct: Optional[float] = None
    status: Optional[str] = None


app = FastAPI(title="UAV/UTM Simulator API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UAV_DB = AgentDB("uav")


def _restore_uav_state() -> None:
    fleet = UAV_DB.get_state("fleet")
    if isinstance(fleet, dict):
        SIM.load_fleet_snapshot(fleet)
    utm_state = UAV_DB.get_state("utm_store")
    if isinstance(utm_state, dict):
        UTM_SERVICE.load_state(utm_state)
    net_state = UAV_DB.get_state("network_service")
    if isinstance(net_state, dict):
        NETWORK_MISSION_SERVICE.load_state(net_state)


def _persist_uav_state() -> None:
    UAV_DB.set_state("fleet", SIM.fleet_snapshot())
    UAV_DB.set_state("utm_store", UTM_SERVICE.export_state())
    UAV_DB.set_state("network_service", NETWORK_MISSION_SERVICE.export_state())


def _log_uav_action(action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    _persist_uav_state()
    return UAV_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)


_restore_uav_state()


def _default_time_window() -> tuple[str, str]:
    now = datetime.now(timezone.utc) + timedelta(minutes=2)
    end = now + timedelta(minutes=20)
    return (
        now.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def _sim_waypoints(uav_id: str) -> tuple[str, list[dict]]:
    sim = SIM.status(uav_id)
    route_id = str(sim.get("route_id", "route-1"))
    waypoints = list(sim.get("waypoints", [])) if isinstance(sim.get("waypoints"), list) else []
    return route_id, waypoints


def _geofence_check_from_waypoints(*, uav_id: str, route_id: str, airspace_segment: str, waypoints: list[dict]) -> Dict[str, Any]:
    bounds = {"sector-A3": {"x": [0, 400], "y": [0, 300], "z": [0, 120]}}
    seg = bounds.get(airspace_segment, {"x": [-1e9, 1e9], "y": [-1e9, 1e9], "z": [0, 120]})
    out_of_bounds = []
    for i, wp in enumerate(waypoints):
        x = float(wp.get("x", 0.0))
        y = float(wp.get("y", 0.0))
        z = float(wp.get("z", 0.0))
        if not (seg["x"][0] <= x <= seg["x"][1] and seg["y"][0] <= y <= seg["y"][1] and seg["z"][0] <= z <= seg["z"][1]):
            out_of_bounds.append({"index": i, "wp": {"x": x, "y": y, "z": z}})
    nfz = UTM_SERVICE.check_no_fly_zones(waypoints)
    return {
        "uav_id": uav_id,
        "route_id": route_id,
        "airspace_segment": airspace_segment,
        "geofence_ok": len(out_of_bounds) == 0 and nfz.get("ok", False),
        "out_of_bounds": out_of_bounds,
        "no_fly_zone": nfz,
    }


def _json_text(value: Any, max_len: int = 20000) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=True)
    except Exception:
        text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _extract_first_json_object(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(text[start:], start=start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


_UAV_COPILOT_OLLAMA_MODEL: Any | None = None


def _ollama_model() -> Any | None:
    global _UAV_COPILOT_OLLAMA_MODEL
    if _UAV_COPILOT_OLLAMA_MODEL is not None:
        return _UAV_COPILOT_OLLAMA_MODEL
    if ChatOllama is None:
        return None
    model_name = str(os.getenv("UAV_COPILOT_OLLAMA_MODEL", MODEL) or MODEL).strip()
    base_url = str(os.getenv("UAV_COPILOT_OLLAMA_URL", OLLAMA_URL) or OLLAMA_URL).strip()
    try:
        _UAV_COPILOT_OLLAMA_MODEL = ChatOllama(model=model_name, base_url=base_url, temperature=0)
        return _UAV_COPILOT_OLLAMA_MODEL
    except Exception:
        return None


def _chat_completion_json(*, system_prompt: str, user_payload: Dict[str, Any]) -> Dict[str, Any]:
    model_obj = _ollama_model()
    model = str(os.getenv("UAV_COPILOT_OLLAMA_MODEL", MODEL) or MODEL).strip()
    if model_obj is None:
        return {"status": "unavailable", "error": "Ollama model not available (langchain_ollama missing or Ollama not reachable)"}
    try:
        resp = model_obj.invoke(
            [
                ("system", system_prompt),
                ("human", _json_text(user_payload, max_len=50000)),
            ]
        )
        content = getattr(resp, "content", None)
        text = content if isinstance(content, str) else _json_text(content)
        parsed = _extract_first_json_object(text or "")
        if not isinstance(parsed, dict):
            return {"status": "error", "model": model, "raw": text, "error": "LLM response was not valid JSON"}
        return {"status": "success", "model": model, "raw": text, "parsed": parsed}
    except Exception as e:
        return {"status": "error", "model": model, "error": str(e)}


def _build_copilot_context(payload: UavAgentChatPayload, *, route_id: str, effective_waypoints: list[dict]) -> Dict[str, Any]:
    sim_state = SIM.status(payload.uav_id)
    network_state_full = NETWORK_MISSION_SERVICE.get_state(
        airspace_segment=payload.airspace_segment,
        selected_uav_id=payload.uav_id,
    )
    network_state = network_state_full.get("result") if isinstance(network_state_full, dict) else None
    return {
        "mission": {
            "uav_id": payload.uav_id,
            "airspace_segment": payload.airspace_segment,
            "prompt": payload.prompt,
            "route_id": route_id,
            "optimization_profile": payload.optimization_profile,
            "auto_verify": payload.auto_verify,
            "auto_network_optimize": payload.auto_network_optimize,
            "requested_network_mode": payload.network_mode,
        },
        "waypoints": effective_waypoints,
        "utm": {
            "weather": UTM_SERVICE.get_weather(payload.airspace_segment),
            "no_fly_zones": list(UTM_SERVICE.no_fly_zones),
            "regulations": dict(UTM_SERVICE.regulations),
        },
        "network": network_state,
        "uav": sim_state,
    }


def _normalize_copilot_actions(plan: Dict[str, Any], payload: UavAgentChatPayload) -> list[Dict[str, Any]]:
    raw_actions = plan.get("actions")
    if not isinstance(raw_actions, list):
        raw_actions = []
    out: list[Dict[str, Any]] = []
    for rec in raw_actions[:5]:
        if not isinstance(rec, dict):
            continue
        tool = str(rec.get("tool", rec.get("action", "")) or "").strip().lower()
        args = rec.get("arguments", rec.get("args", {}))
        if not isinstance(args, dict):
            args = {}
        if tool in {"replan", "replan_route", "route_replan"}:
            out.append({"tool": "replan_route", "args": args})
        elif tool in {"verify", "verify_flight_plan", "utm_verify"}:
            out.append({"tool": "verify_flight_plan", "args": args})
        elif tool in {"network_optimize", "optimize_network"}:
            out.append({"tool": "network_optimize", "args": args})
        elif tool in {"hold", "uav_hold"}:
            out.append({"tool": "hold", "args": args})
        elif tool in {"noop", "none", "respond_only"}:
            out.append({"tool": "noop", "args": args})
    has_verify = any(a["tool"] == "verify_flight_plan" for a in out)
    has_net = any(a["tool"] == "network_optimize" for a in out)
    if payload.auto_verify and not has_verify:
        out.append({"tool": "verify_flight_plan", "args": {"reason": "auto_verify_policy"}})
    if payload.auto_network_optimize and not has_net:
        out.append({"tool": "network_optimize", "args": {"reason": "auto_network_optimize_policy"}})
    return out or [{"tool": "noop", "args": {}}]


def _llm_plan_actions(payload: UavAgentChatPayload, context: Dict[str, Any]) -> Dict[str, Any]:
    system_prompt = (
        "You are a UAV copilot planner. "
        "Decide a short sequence of actions for a UAV mission assistant. "
        "Available tools: replan_route, verify_flight_plan, network_optimize, hold, noop. "
        "Use at most 4 actions. Prefer safe behavior. "
        "Return ONLY JSON with keys: assistant_response (string), actions (array). "
        "Each action item must be an object with tool and arguments. "
        "Use network_optimize.mode in {coverage,qos,power} if chosen. "
        "If no tool is needed, return noop."
    )
    user_payload = {
        "task": "plan_uav_copilot_actions",
        "context": context,
        "hints": {
            "route_replan_tool": "uav_replan_route_via_utm_nfz",
            "verify_tool": "UTM_SERVICE.verify_flight_plan",
            "network_tool": "NETWORK_MISSION_SERVICE.apply_optimization",
            "hold_tool": "uav_hold",
        },
    }
    resp = _chat_completion_json(system_prompt=system_prompt, user_payload=user_payload)
    if resp.get("status") != "success":
        return resp
    parsed = resp.get("parsed") if isinstance(resp.get("parsed"), dict) else {}
    return {
        "status": "success",
        "model": resp.get("model"),
        "raw": resp.get("raw"),
        "assistant_response": str(parsed.get("assistant_response", "") or "").strip(),
        "actions": _normalize_copilot_actions(parsed, payload),
    }


def _summarize_tool_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out: Dict[str, Any] = {"status": result.get("status", "unknown")}
    r = result.get("result")
    if isinstance(r, dict):
        for key in ("route_id", "uav_id", "approved", "ok", "mode"):
            if key in r:
                out[key] = r.get(key)
    for key in ("approved", "mode", "tool"):
        if key in result and key not in out:
            out[key] = result.get(key)
    if "error" in result:
        out["error"] = result.get("error")
    return out


def _execute_copilot_actions(
    payload: UavAgentChatPayload,
    *,
    prompt: str,
    route_id: str,
    effective_waypoints: list[dict],
    actions: list[Dict[str, Any]],
) -> Dict[str, Any]:
    messages: List[str] = []
    tool_trace: List[Dict[str, Any]] = []
    replan_result: Dict[str, Any] | None = None
    verify_result: Dict[str, Any] | None = None
    net_opt_result: Dict[str, Any] | None = None
    chosen_network_mode: str | None = None

    for idx, action in enumerate(actions, start=1):
        tool = str(action.get("tool", "") or "")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        if tool == "noop":
            tool_trace.append({"step": idx, "tool": "noop", "status": "skipped"})
            continue

        if tool == "replan_route":
            replan_args = {
                "uav_id": payload.uav_id,
                "airspace_segment": payload.airspace_segment,
                "user_request": str(args.get("user_request", prompt) or prompt),
                "route_id": str(args.get("route_id", route_id) or route_id),
                "waypoints": effective_waypoints,
                "optimization_profile": str(args.get("optimization_profile", payload.optimization_profile) or payload.optimization_profile),
            }
            replan_result = uav_replan_route_via_utm_nfz.invoke(replan_args)
            step_status = str(replan_result.get("status", "unknown")) if isinstance(replan_result, dict) else "unknown"
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "uav_replan_route_via_utm_nfz",
                    "status": step_status,
                    "args": {"optimization_profile": replan_args["optimization_profile"]},
                    "summary": _summarize_tool_result(replan_result),
                }
            )
            if step_status == "success":
                rr = replan_result.get("result", {}) if isinstance(replan_result, dict) else {}
                changes = rr.get("changes") if isinstance(rr, dict) else None
                messages.append(f"Replanned route ({replan_args['optimization_profile']}); changes={len(changes) if isinstance(changes, list) else 0}.")
                sim_now = SIM.status(payload.uav_id)
                effective_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else effective_waypoints
            else:
                messages.append("Route replan failed.")
            continue

        if tool == "verify_flight_plan":
            sim_now = SIM.status(payload.uav_id)
            current_route_id = str(sim_now.get("route_id", route_id))
            current_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else []
            verify_result = UTM_SERVICE.verify_flight_plan(
                uav_id=payload.uav_id,
                airspace_segment=payload.airspace_segment,
                route_id=current_route_id,
                waypoints=current_waypoints,
                operator_license_id=str(args.get("operator_license_id", "op-001") or "op-001"),
                required_license_class=str(args.get("required_license_class", "VLOS") or "VLOS"),
                requested_speed_mps=float(args.get("requested_speed_mps", sim_now.get("velocity_mps", 12.0) or 12.0)),
                planned_start_at=str(args.get("planned_start_at")) if args.get("planned_start_at") else None,
                planned_end_at=str(args.get("planned_end_at")) if args.get("planned_end_at") else None,
            )
            approved = bool(verify_result.get("approved")) if isinstance(verify_result, dict) else False
            messages.append(f"UTM verification: {'approved' if approved else 'not approved'}.")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "utm_verify_flight_plan",
                    "status": "success",
                    "approved": approved,
                    "summary": _summarize_tool_result({"status": "success", "approved": approved, "result": verify_result}),
                }
            )
            continue

        if tool == "network_optimize":
            mode = str(args.get("mode", payload.network_mode or "") or "").lower().strip()
            if mode not in {"coverage", "qos", "power"}:
                if any(k in prompt.lower() for k in ["qos", "latency", "loss", "video"]):
                    mode = "qos"
                elif "power" in prompt.lower():
                    mode = "power"
                else:
                    mode = "coverage"
            chosen_network_mode = mode
            net_opt_result = NETWORK_MISSION_SERVICE.apply_optimization(mode=mode)
            ok = isinstance(net_opt_result, dict) and net_opt_result.get("status") == "success"
            messages.append(f"{'Applied' if ok else 'Failed'} network optimization mode: {mode}.")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "network_apply_optimization",
                    "status": "success" if ok else "error",
                    "mode": mode,
                    "summary": _summarize_tool_result(net_opt_result),
                }
            )
            continue

        if tool == "hold":
            reason = str(args.get("reason", "copilot_safety_hold") or "copilot_safety_hold")
            hold_result = uav_hold.invoke({"uav_id": payload.uav_id, "reason": reason})
            ok = isinstance(hold_result, dict) and hold_result.get("status") == "success"
            messages.append("Placed UAV in hold." if ok else "Failed to place UAV in hold.")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "uav_hold",
                    "status": "success" if ok else "error",
                    "reason": reason,
                    "summary": _summarize_tool_result(hold_result),
                }
            )
            continue

        tool_trace.append({"step": idx, "tool": tool, "status": "skipped", "reason": "unsupported_action"})

    return {
        "messages": messages,
        "toolTrace": tool_trace,
        "replan": replan_result,
        "utmVerify": verify_result,
        "networkOptimization": net_opt_result,
        "networkMode": chosen_network_mode,
    }


def _llm_summarize_outcome(
    *,
    payload: UavAgentChatPayload,
    context_before: Dict[str, Any],
    tool_trace: list[Dict[str, Any]],
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    system_prompt = (
        "You are a UAV copilot assistant. Summarize what happened after tool execution. "
        "Return ONLY JSON with keys: response (string), messages (array of short strings). "
        "Do not invent tool results."
    )
    user_payload = {
        "task": "summarize_uav_copilot_outcome",
        "prompt": payload.prompt,
        "context_before": context_before,
        "tool_trace": tool_trace,
        "execution": execution,
    }
    return _chat_completion_json(system_prompt=system_prompt, user_payload=user_payload)


def _run_uav_agent_chat_heuristic(payload: UavAgentChatPayload) -> Dict[str, Any]:
    prompt = (payload.prompt or "").strip()
    sim_before = SIM.status(payload.uav_id)
    route_id = payload.route_id or str(sim_before.get("route_id", "route-1"))
    input_waypoints = [w.model_dump() for w in payload.waypoints] if payload.waypoints else None
    effective_waypoints = input_waypoints if input_waypoints else (list(sim_before.get("waypoints", [])) if isinstance(sim_before.get("waypoints"), list) else [])
    if len(effective_waypoints) >= 2:
        SIM.plan_route(payload.uav_id, route_id=route_id, waypoints=effective_waypoints)
    else:
        result = {"status": "error", "error": "route_requires_at_least_two_waypoints"}
        sync = _log_uav_action("agent_chat", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
        return {"status": "error", "sync": sync, "result": result}

    p = prompt.lower()
    wants_route_opt = any(k in p for k in ["replan", "path", "route", "optimiz"])
    wants_network = payload.auto_network_optimize or any(k in p for k in ["coverage", "signal", "network", "sinr", "qos", "latency", "power"])
    net_mode = str(payload.network_mode or "").lower().strip()
    if net_mode not in {"coverage", "qos", "power"}:
        net_mode = "coverage"
        if any(k in p for k in ["qos", "latency", "loss", "video"]):
            net_mode = "qos"
        elif "power" in p:
            net_mode = "power"

    messages: List[str] = []
    tool_trace: List[Dict[str, Any]] = []
    replan_result: Dict[str, Any] | None = None
    if wants_route_opt:
        replan_result = uav_replan_route_via_utm_nfz.invoke(
            {
                "uav_id": payload.uav_id,
                "airspace_segment": payload.airspace_segment,
                "user_request": prompt,
                "route_id": route_id,
                "waypoints": effective_waypoints,
                "optimization_profile": payload.optimization_profile,
            }
        )
        if isinstance(replan_result, dict) and replan_result.get("status") == "success":
            rr = replan_result.get("result")
            if isinstance(rr, dict):
                changes = rr.get("changes")
                n_changes = len(changes) if isinstance(changes, list) else 0
                deletions = rr.get("waypoint_deletions")
                n_deleted = len(deletions) if isinstance(deletions, list) else 0
                messages.append(
                    f"Replanned route ({payload.optimization_profile}) to avoid UTM no-fly zones "
                    f"({n_changes} changes, {n_deleted} waypoint removals)."
                )
            tool_trace.append({"tool": "uav_replan_route_via_utm_nfz", "status": "success", "profile": payload.optimization_profile})
        else:
            messages.append("Route replan failed.")
            tool_trace.append({"tool": "uav_replan_route_via_utm_nfz", "status": "error"})
    else:
        messages.append("Kept current route (no replan requested).")
        tool_trace.append({"tool": "route_replan", "status": "skipped"})

    verify_result: Dict[str, Any] | None = None
    if payload.auto_verify:
        sim_now = SIM.status(payload.uav_id)
        current_route_id = str(sim_now.get("route_id", route_id))
        current_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else []
        verify_result = UTM_SERVICE.verify_flight_plan(
            uav_id=payload.uav_id,
            airspace_segment=payload.airspace_segment,
            route_id=current_route_id,
            waypoints=current_waypoints,
            operator_license_id="op-001",
            required_license_class="VLOS",
            requested_speed_mps=float(sim_now.get("velocity_mps", 12.0) or 12.0),
        )
        messages.append(f"UTM verification: {'approved' if verify_result.get('approved') else 'not approved'}.")
        tool_trace.append({"tool": "utm_verify_flight_plan", "status": "success", "approved": bool(verify_result.get("approved"))})

    net_opt_result: Dict[str, Any] | None = None
    if wants_network:
        net_opt_result = NETWORK_MISSION_SERVICE.apply_optimization(mode=net_mode)
        if isinstance(net_opt_result, dict) and net_opt_result.get("status") == "success":
            messages.append(f"Applied network optimization mode: {net_mode}.")
            tool_trace.append({"tool": "network_apply_optimization", "status": "success", "mode": net_mode})
        else:
            messages.append("Network optimization failed.")
            tool_trace.append({"tool": "network_apply_optimization", "status": "error", "mode": net_mode})

    network_state = NETWORK_MISSION_SERVICE.get_state(airspace_segment=payload.airspace_segment, selected_uav_id=payload.uav_id)
    sim_after = SIM.status(payload.uav_id)
    agent_result = {
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "prompt": prompt,
        "optimizationProfile": payload.optimization_profile,
        "networkMode": net_mode if wants_network else None,
        "autoVerify": payload.auto_verify,
        "autoNetworkOptimize": wants_network,
        "messages": messages,
        "toolTrace": tool_trace,
        "uav": sim_after,
        "replan": replan_result,
        "utmVerify": verify_result,
        "networkOptimization": net_opt_result,
        "networkState": network_state.get("result") if isinstance(network_state, dict) else None,
        "copilot": {
            "mode": "heuristic",
            "llm": {
                "enabled": False,
                "reason": "Ollama planner unavailable (langchain_ollama missing or Ollama not reachable)",
            },
        },
    }
    sync = _log_uav_action("agent_chat", payload=payload.model_dump(), result=agent_result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": agent_result}


@app.get("/api/uav/sim/state")
def get_sim_state(uav_id: str = "uav-1") -> Dict[str, Any]:
    status = uav_status.invoke({"uav_id": uav_id})
    route = status.get("result", {}) if isinstance(status, dict) else {}
    airspace = "sector-A3"
    return {
        "status": "success",
        "sync": UAV_DB.get_sync(),
        "uav": route,
        "utm": {
            "weather": UTM_SERVICE.get_weather(airspace),
            "no_fly_zones": UTM_SERVICE.no_fly_zones,
            "regulations": UTM_SERVICE.regulations,
            "licenses": UTM_SERVICE.operator_licenses,
        },
    }


@app.get("/api/uav/sim/fleet")
def get_sim_fleet() -> Dict[str, Any]:
    return {"status": "success", "sync": UAV_DB.get_sync(), "result": {"fleet": SIM.fleet_snapshot()}}


@app.get("/api/uav/sync")
def get_uav_sync(limit_actions: int = 5) -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "sync": UAV_DB.get_sync(),
            "recentActions": UAV_DB.recent_actions(limit_actions),
        },
    }


@app.post("/api/uav/sim/plan")
def post_plan_route(payload: PlanRoutePayload) -> Dict[str, Any]:
    result = uav_plan_route.invoke(
        {
            "uav_id": payload.uav_id,
            "route_id": payload.route_id,
            "waypoints": [w.model_dump() for w in payload.waypoints],
        }
    )
    sync = _log_uav_action("plan_route", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/replan-via-utm-nfz")
def post_replan_via_utm_nfz(payload: ReplanPayload) -> Dict[str, Any]:
    result = uav_replan_route_via_utm_nfz.invoke(payload.model_dump())
    sync = _log_uav_action("replan_via_utm_nfz", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/agent/chat")
def post_uav_agent_chat(payload: UavAgentChatPayload) -> Dict[str, Any]:
    prompt = (payload.prompt or "").strip()
    sim_before = SIM.status(payload.uav_id)
    route_id = payload.route_id or str(sim_before.get("route_id", "route-1"))
    input_waypoints = [w.model_dump() for w in payload.waypoints] if payload.waypoints else None
    effective_waypoints = input_waypoints if input_waypoints else (
        list(sim_before.get("waypoints", [])) if isinstance(sim_before.get("waypoints"), list) else []
    )
    if len(effective_waypoints) < 2:
        result = {"status": "error", "error": "route_requires_at_least_two_waypoints"}
        sync = _log_uav_action("agent_chat", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
        return {"status": "error", "sync": sync, "result": result}
    SIM.plan_route(payload.uav_id, route_id=route_id, waypoints=effective_waypoints)

    workflow = run_copilot_workflow(
        {
            "uav_id": payload.uav_id,
            "airspace_segment": payload.airspace_segment,
            "prompt": prompt,
            "route_id": route_id,
            "effective_waypoints": effective_waypoints,
            "optimization_profile": payload.optimization_profile,
            "auto_verify": payload.auto_verify,
            "auto_network_optimize": payload.auto_network_optimize,
            "network_mode": payload.network_mode,
        }
    )
    if workflow.get("status") != "success" or not isinstance(workflow.get("result"), dict):
        return _run_uav_agent_chat_heuristic(payload)
    agent_result = workflow["result"]
    sync = _log_uav_action("agent_chat", payload=payload.model_dump(), result=agent_result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": agent_result}


@app.post("/api/uav/sim/geofence-submit")
def post_geofence_submit(uav_id: str = "uav-1", airspace_segment: str = "sector-A3") -> Dict[str, Any]:
    payload = {"uav_id": uav_id, "airspace_segment": airspace_segment}
    result = uav_submit_route_to_utm_geofence_check.invoke(payload)
    sync = _log_uav_action("geofence_submit", payload=payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/request-approval")
def post_request_approval(payload: ApprovalPayload) -> Dict[str, Any]:
    start_at, end_at = _default_time_window()
    req = {
        "uav_id": payload.uav_id,
        "airspace_segment": payload.airspace_segment,
        "operator_license_id": payload.operator_license_id,
        "required_license_class": payload.required_license_class,
        "requested_speed_mps": payload.requested_speed_mps,
        "planned_start_at": payload.planned_start_at or start_at,
        "planned_end_at": payload.planned_end_at or end_at,
    }
    result = uav_request_utm_approval.invoke(
        req
    )
    sync = _log_uav_action("request_approval", payload=req, result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/launch")
def post_launch(uav_id: str = "uav-1") -> Dict[str, Any]:
    payload = {"uav_id": uav_id, "require_utm_approval": True}
    result = uav_launch.invoke(payload)
    sync = _log_uav_action("launch", payload=payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/step")
def post_step(payload: StepPayload) -> Dict[str, Any]:
    result = uav_sim_step.invoke({"uav_id": payload.uav_id, "ticks": payload.ticks})
    sync = _log_uav_action("step", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/hold")
def post_hold(payload: HoldPayload) -> Dict[str, Any]:
    result = uav_hold.invoke({"uav_id": payload.uav_id, "reason": payload.reason})
    sync = _log_uav_action("hold", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/resume")
def post_resume(uav_id: str = "uav-1") -> Dict[str, Any]:
    payload = {"uav_id": uav_id}
    result = uav_resume.invoke(payload)
    sync = _log_uav_action("resume", payload=payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/rth")
def post_return_to_home(uav_id: str = "uav-1") -> Dict[str, Any]:
    payload = {"uav_id": uav_id}
    result = uav_return_to_home.invoke(payload)
    sync = _log_uav_action("rth", payload=payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/uav/sim/land")
def post_land(uav_id: str = "uav-1") -> Dict[str, Any]:
    payload = {"uav_id": uav_id}
    result = uav_land.invoke(payload)
    sync = _log_uav_action("land", payload=payload, result=result, entity_id=uav_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.get("/api/utm/weather")
def get_weather(airspace_segment: str = "sector-A3") -> Dict[str, Any]:
    return {"status": "success", "result": UTM_SERVICE.check_weather(airspace_segment)}


@app.post("/api/utm/weather")
def set_weather(payload: WeatherPayload) -> Dict[str, Any]:
    weather = UTM_SERVICE.set_weather(
        payload.airspace_segment,
        wind_mps=payload.wind_mps,
        visibility_km=payload.visibility_km,
        precip_mmph=payload.precip_mmph,
        storm_alert=payload.storm_alert,
    )
    result = {"airspace_segment": payload.airspace_segment, "weather": weather}
    sync = _log_uav_action("utm_set_weather", payload=payload.model_dump(), result=result, entity_id=payload.airspace_segment)
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/utm/nfz")
def list_no_fly_zones() -> Dict[str, Any]:
    return {"status": "success", "result": {"no_fly_zones": UTM_SERVICE.no_fly_zones}}


@app.post("/api/utm/nfz")
def add_no_fly_zone(payload: NoFlyZonePayload) -> Dict[str, Any]:
    result = UTM_SERVICE.add_no_fly_zone(**payload.model_dump())
    sync = _log_uav_action("utm_add_nfz", payload=payload.model_dump(), result=result, entity_id=str(result.get("zone_id", "")))
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/license")
def register_license(payload: LicensePayload) -> Dict[str, Any]:
    result = UTM_SERVICE.register_operator_license(**payload.model_dump())
    sync = _log_uav_action("utm_register_license", payload=payload.model_dump(), result=result, entity_id=payload.operator_license_id)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/corridor/reserve")
def reserve_corridor(payload: CorridorPayload) -> Dict[str, Any]:
    result = {"uav_id": payload.uav_id, "airspace_segment": payload.airspace_segment, "reserved": True}
    sync = _log_uav_action("utm_reserve_corridor", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/checks/route")
def route_checks(payload: RouteCheckPayload) -> Dict[str, Any]:
    route_id, waypoints = _sim_waypoints(payload.uav_id)
    rid = payload.route_id or route_id
    geofence = _geofence_check_from_waypoints(
        uav_id=payload.uav_id,
        route_id=rid,
        airspace_segment=payload.airspace_segment,
        waypoints=waypoints,
    )
    return {
        "status": "success",
        "result": {
            "uav_id": payload.uav_id,
            "route_id": rid,
            "airspace_segment": payload.airspace_segment,
            "waypoints_total": len(waypoints),
            "geofence": geofence,
            "no_fly_zone": UTM_SERVICE.check_no_fly_zones(waypoints),
            "regulations": UTM_SERVICE.check_regulations(waypoints, requested_speed_mps=payload.requested_speed_mps),
        },
    }


@app.post("/api/utm/checks/time-window")
def check_time_window(payload: TimeWindowCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_time_window(payload.planned_start_at, payload.planned_end_at)
    sync = _log_uav_action("utm_check_time_window", payload=payload.model_dump(), result=result)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/checks/license")
def check_license(payload: LicenseCheckPayload) -> Dict[str, Any]:
    result = UTM_SERVICE.check_operator_license(
        operator_license_id=payload.operator_license_id,
        required_class=payload.required_license_class,
    )
    sync = _log_uav_action("utm_check_license", payload=payload.model_dump(), result=result, entity_id=payload.operator_license_id)
    return {"status": "success", "sync": sync, "result": result}


@app.post("/api/utm/verify-from-uav")
def verify_from_uav(payload: VerifyFromUavPayload) -> Dict[str, Any]:
    route_id, waypoints = _sim_waypoints(payload.uav_id)
    result = UTM_SERVICE.verify_flight_plan(
        uav_id=payload.uav_id,
        airspace_segment=payload.airspace_segment,
        route_id=route_id,
        waypoints=waypoints,
        requested_speed_mps=payload.requested_speed_mps,
        planned_start_at=payload.planned_start_at,
        planned_end_at=payload.planned_end_at,
        operator_license_id=payload.operator_license_id,
        required_license_class=payload.required_license_class,
    )
    sync = _log_uav_action("utm_verify_from_uav", payload=payload.model_dump(), result=result, entity_id=payload.uav_id)
    return {"status": "success", "sync": sync, "result": result}


@app.get("/api/network/mission/state")
def get_network_mission_state(airspace_segment: str = "sector-A3", selected_uav_id: Optional[str] = None) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=selected_uav_id)
    if isinstance(result, dict):
        result["sync"] = UAV_DB.get_sync()
    return result


@app.post("/api/network/mission/tick")
def post_network_mission_tick(payload: NetworkTickPayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.tick(steps=payload.steps)
    sync = _log_uav_action("network_tick", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/network/optimize")
def post_network_optimize(payload: NetworkOptimizePayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.apply_optimization(
        mode=payload.mode,
        coverage_target_pct=payload.coverage_target_pct,
        max_tx_cap_dbm=payload.max_tx_cap_dbm,
        qos_priority_weight=payload.qos_priority_weight,
    )
    sync = _log_uav_action("network_optimize", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/network/base-station/update")
def post_network_base_station_update(payload: NetworkBaseStationUpdatePayload) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    bs_id = str(updates.pop("bs_id"))
    result = NETWORK_MISSION_SERVICE.update_base_station(bs_id, **updates)
    sync = _log_uav_action("network_bs_update", payload=payload.model_dump(), result=result, entity_id=bs_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result
