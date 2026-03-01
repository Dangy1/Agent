from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from .copilot_utils import _chat_completion_json, _utm_nfz_conflict_feedback
from .simulator import SIM
from .tools import uav_hold, uav_replan_route_via_utm_nfz
from network_agent.service import NETWORK_MISSION_SERVICE
from utm_agent.service import UTM_SERVICE


class CopilotState(TypedDict, total=False):
    payload: Dict[str, Any]
    context_before: Dict[str, Any]
    planner: Dict[str, Any]
    execution: Dict[str, Any]
    summary: Dict[str, Any]
    result: Dict[str, Any]


def _build_copilot_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    uav_id = str(payload.get("uav_id", "uav-1"))
    airspace_segment = str(payload.get("airspace_segment", "sector-A3"))
    route_id = str(payload.get("route_id", "route-1"))
    effective_waypoints = list(payload.get("effective_waypoints") or [])
    network_state_full = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=uav_id)
    network_state = network_state_full.get("result") if isinstance(network_state_full, dict) else None
    return {
        "mission": {
            "uav_id": uav_id,
            "airspace_segment": airspace_segment,
            "prompt": payload.get("prompt", ""),
            "route_id": route_id,
            "optimization_profile": payload.get("optimization_profile", "balanced"),
            "auto_verify": bool(payload.get("auto_verify", True)),
            "auto_network_optimize": bool(payload.get("auto_network_optimize", True)),
            "requested_network_mode": payload.get("network_mode"),
        },
        "waypoints": effective_waypoints,
        "utm": {
            "weather": UTM_SERVICE.get_weather(airspace_segment),
            "no_fly_zones": list(UTM_SERVICE.no_fly_zones),
            "regulations": dict(UTM_SERVICE.regulations),
        },
        "network": network_state,
        "uav": SIM.status(uav_id),
    }


def _normalize_actions(plan: Dict[str, Any], payload: Dict[str, Any]) -> list[Dict[str, Any]]:
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
    if bool(payload.get("auto_verify", True)) and not has_verify:
        out.append({"tool": "verify_flight_plan", "args": {"reason": "auto_verify_policy"}})
    if bool(payload.get("auto_network_optimize", True)) and not has_net:
        out.append({"tool": "network_optimize", "args": {"reason": "auto_network_optimize_policy"}})
    return out or [{"tool": "noop", "args": {}}]


def _summarize_tool_result(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out: Dict[str, Any] = {"status": result.get("status", "unknown")}
    r = result.get("result")
    if isinstance(r, dict):
        for key in ("route_id", "uav_id", "approved", "ok", "mode"):
            if key in r:
                out[key] = r.get(key)
    if "error" in result:
        out["error"] = result.get("error")
    return out


def _is_dss_conflict_context(text: str) -> bool:
    t = str(text or "").strip().lower()
    return ("dss" in t) and any(k in t for k in ("conflict", "strategic", "blocking"))


def _exec_actions(payload: Dict[str, Any], actions: list[Dict[str, Any]]) -> Dict[str, Any]:
    uav_id = str(payload.get("uav_id", "uav-1"))
    airspace_segment = str(payload.get("airspace_segment", "sector-A3"))
    prompt = str(payload.get("prompt", "") or "")
    route_id = str(payload.get("route_id", "route-1"))
    effective_waypoints = list(payload.get("effective_waypoints") or [])
    optimization_profile = str(payload.get("optimization_profile", "balanced") or "balanced")
    network_mode_hint = str(payload.get("network_mode", "") or "").lower().strip()
    dss_conflict_prompt = _is_dss_conflict_context(prompt)

    messages: List[str] = []
    tool_trace: List[Dict[str, Any]] = []
    replan_result: Dict[str, Any] | None = None
    replan_context: str | None = None
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
                "uav_id": uav_id,
                "airspace_segment": airspace_segment,
                "user_request": str(args.get("user_request", prompt) or prompt),
                "route_id": str(args.get("route_id", route_id) or route_id),
                "waypoints": effective_waypoints,
                "optimization_profile": str(args.get("optimization_profile", optimization_profile) or optimization_profile),
            }
            replan_result = uav_replan_route_via_utm_nfz.invoke(replan_args)
            replan_reason = str(args.get("reason", "") or "").strip().lower()
            replan_context_arg = str(args.get("replan_context", "") or "").strip().lower()
            dss_conflict_replan = (
                dss_conflict_prompt
                or ("dss" in replan_reason and ("conflict" in replan_reason or "strategic" in replan_reason))
                or ("dss" in replan_context_arg and "conflict" in replan_context_arg)
            )
            replan_context = "dss_conflict_mitigation" if dss_conflict_replan else "agent_copilot"
            ok = isinstance(replan_result, dict) and replan_result.get("status") == "success"
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "uav_replan_route_via_utm_nfz",
                    "status": "success" if ok else "error",
                    "reason": "dss_strategic_conflict_mitigation" if dss_conflict_replan else "operator_prompt_replan",
                    "replan_context": replan_context,
                    "args": {"optimization_profile": replan_args["optimization_profile"]},
                    "summary": _summarize_tool_result(replan_result),
                }
            )
            if ok:
                rr = replan_result.get("result", {})
                changes = rr.get("changes") if isinstance(rr, dict) else None
                messages.append(f"Replanned route ({replan_args['optimization_profile']}); changes={len(changes) if isinstance(changes, list) else 0}.")
                sim_now = SIM.status(uav_id)
                effective_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else effective_waypoints
            else:
                messages.append("Route replan failed.")
            continue

        if tool == "verify_flight_plan":
            sim_now = SIM.status(uav_id)
            current_route_id = str(sim_now.get("route_id", route_id))
            current_waypoints = list(sim_now.get("waypoints", [])) if isinstance(sim_now.get("waypoints"), list) else []
            verify_result = UTM_SERVICE.verify_flight_plan(
                uav_id=uav_id,
                airspace_segment=airspace_segment,
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
            conflict_fb = _utm_nfz_conflict_feedback(verify_result)
            decision_obj = verify_result.get("decision") if isinstance(verify_result.get("decision"), dict) else None
            if isinstance(decision_obj, dict):
                for m in decision_obj.get("messages", []) if isinstance(decision_obj.get("messages"), list) else []:
                    messages.append(f"UTM: {str(m)}")
                for s in decision_obj.get("suggestions", []) if isinstance(decision_obj.get("suggestions"), list) else []:
                    messages.append(f"Suggestion: {str(s)}")
            tool_trace.append(
                {
                    "step": idx,
                    "tool": "utm_verify_flight_plan",
                    "status": "success",
                    "approved": approved,
                    "utm_decision": decision_obj,
                    "nfz_conflict_feedback": conflict_fb if conflict_fb["has_conflict"] else None,
                    "summary": _summarize_tool_result({"status": "success", "approved": approved, "result": verify_result}),
                }
            )
            if not approved and conflict_fb["has_conflict"]:
                conflict_hint = conflict_fb["summary"] or "NFZ conflict detected"
                messages.append(f"UTM reported no-fly-zone conflicts at {conflict_hint}. Regenerating route and re-verifying.")
                replan_args = {
                    "uav_id": uav_id,
                    "airspace_segment": airspace_segment,
                    "user_request": f"Avoid no-fly zones and repair route conflicts at {conflict_hint}.",
                    "route_id": current_route_id,
                    "waypoints": current_waypoints,
                    "optimization_profile": optimization_profile,
                }
                corrective_replan = uav_replan_route_via_utm_nfz.invoke(replan_args)
                replan_ok = isinstance(corrective_replan, dict) and corrective_replan.get("status") == "success"
                tool_trace.append(
                    {
                        "step": idx,
                        "tool": "uav_replan_route_via_utm_nfz",
                        "status": "success" if replan_ok else "error",
                        "reason": "auto_repair_after_utm_verify_nfz_conflict",
                        "summary": _summarize_tool_result(corrective_replan),
                        "conflicts": conflict_fb,
                    }
                )
                if replan_ok:
                    sim_fix = SIM.status(uav_id)
                    fixed_route_id = str(sim_fix.get("route_id", current_route_id))
                    fixed_waypoints = list(sim_fix.get("waypoints", [])) if isinstance(sim_fix.get("waypoints"), list) else current_waypoints
                    verify_result = UTM_SERVICE.verify_flight_plan(
                        uav_id=uav_id,
                        airspace_segment=airspace_segment,
                        route_id=fixed_route_id,
                        waypoints=fixed_waypoints,
                        operator_license_id=str(args.get("operator_license_id", "op-001") or "op-001"),
                        required_license_class=str(args.get("required_license_class", "VLOS") or "VLOS"),
                        requested_speed_mps=float(args.get("requested_speed_mps", sim_fix.get("velocity_mps", 12.0) or 12.0)),
                        planned_start_at=str(args.get("planned_start_at")) if args.get("planned_start_at") else None,
                        planned_end_at=str(args.get("planned_end_at")) if args.get("planned_end_at") else None,
                    )
                    approved = bool(verify_result.get("approved")) if isinstance(verify_result, dict) else False
                    messages.append(f"UTM re-verification after regeneration: {'approved' if approved else 'not approved'}.")
                    decision_obj2 = verify_result.get("decision") if isinstance(verify_result.get("decision"), dict) else None
                    if isinstance(decision_obj2, dict):
                        for m in decision_obj2.get("messages", []) if isinstance(decision_obj2.get("messages"), list) else []:
                            messages.append(f"UTM: {str(m)}")
                        for s in decision_obj2.get("suggestions", []) if isinstance(decision_obj2.get("suggestions"), list) else []:
                            messages.append(f"Suggestion: {str(s)}")
                    tool_trace.append(
                        {
                            "step": idx,
                            "tool": "utm_verify_flight_plan",
                            "status": "success",
                            "approved": approved,
                            "reason": "post_auto_repair_reverify",
                            "utm_decision": decision_obj2,
                            "nfz_conflict_feedback": _utm_nfz_conflict_feedback(verify_result),
                            "summary": _summarize_tool_result({"status": "success", "approved": approved, "result": verify_result}),
                        }
                    )
            continue

        if tool == "network_optimize":
            mode = str(args.get("mode", network_mode_hint) or "").lower().strip()
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
            hold_result = uav_hold.invoke({"uav_id": uav_id, "reason": reason})
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
        "replanContext": replan_context,
        "utmVerify": verify_result,
        "networkOptimization": net_opt_result,
        "networkMode": chosen_network_mode,
    }


def _plan_node(state: CopilotState) -> CopilotState:
    payload = dict(state.get("payload") or {})
    context = _build_copilot_context(payload)
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
    planner_raw = _chat_completion_json(
        system_prompt=system_prompt,
        user_payload={
            "task": "plan_uav_copilot_actions",
            "context": context,
            "hints": {
                "route_replan_tool": "uav_replan_route_via_utm_nfz",
                "verify_tool": "UTM_SERVICE.verify_flight_plan",
                "network_tool": "NETWORK_MISSION_SERVICE.apply_optimization",
                "hold_tool": "uav_hold",
            },
        },
        unavailable_error_message="Ollama planner unavailable (langchain_ollama missing or Ollama not reachable)",
    )
    planner = dict(planner_raw)
    if planner.get("status") == "success":
        parsed = planner.get("parsed") if isinstance(planner.get("parsed"), dict) else {}
        planner["assistant_response"] = str(parsed.get("assistant_response", "") or "").strip()
        planner["actions"] = _normalize_actions(parsed, payload)
    return {"context_before": context, "planner": planner}


def _execute_node(state: CopilotState) -> CopilotState:
    payload = dict(state.get("payload") or {})
    planner = dict(state.get("planner") or {})
    if planner.get("status") != "success":
        return {"execution": {"status": "skipped", "reason": "planner_unavailable"}}
    actions = planner.get("actions") if isinstance(planner.get("actions"), list) else []
    return {"execution": _exec_actions(payload, actions)}


def _summary_node(state: CopilotState) -> CopilotState:
    payload = dict(state.get("payload") or {})
    planner = dict(state.get("planner") or {})
    execution = dict(state.get("execution") or {})
    if planner.get("status") != "success":
        return {"summary": {"status": "skipped", "reason": "planner_unavailable"}}
    tool_trace = execution.get("toolTrace") if isinstance(execution.get("toolTrace"), list) else []
    summary = _chat_completion_json(
        system_prompt=(
            "You are a UAV copilot assistant. Summarize what happened after tool execution. "
            "Return ONLY JSON with keys: response (string), messages (array of short strings). "
            "Do not invent tool results."
        ),
        user_payload={
            "task": "summarize_uav_copilot_outcome",
            "prompt": payload.get("prompt", ""),
            "context_before": state.get("context_before") or {},
            "tool_trace": tool_trace,
            "execution": execution,
        },
        unavailable_error_message="Ollama planner unavailable (langchain_ollama missing or Ollama not reachable)",
    )
    return {"summary": summary}


def _finalize_node(state: CopilotState) -> CopilotState:
    payload = dict(state.get("payload") or {})
    planner = dict(state.get("planner") or {})
    execution = dict(state.get("execution") or {})
    summary = dict(state.get("summary") or {})

    assistant_response = str(planner.get("assistant_response", "") or "").strip()
    llm_messages: List[str] = []
    if summary.get("status") == "success" and isinstance(summary.get("parsed"), dict):
        parsed = summary["parsed"]
        assistant_response = str(parsed.get("response", assistant_response) or assistant_response).strip()
        raw_msgs = parsed.get("messages")
        if isinstance(raw_msgs, list):
            llm_messages = [str(m) for m in raw_msgs if str(m).strip()][:6]

    messages = [m for m in llm_messages if m] or list(execution.get("messages", []))
    if assistant_response:
        messages = [assistant_response, *messages]

    uav_id = str(payload.get("uav_id", "uav-1"))
    airspace_segment = str(payload.get("airspace_segment", "sector-A3"))
    network_state = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=uav_id)
    sim_after = SIM.status(uav_id)
    actions = planner.get("actions") if isinstance(planner.get("actions"), list) else []

    result = {
        "uav_id": uav_id,
        "airspace_segment": airspace_segment,
        "prompt": str(payload.get("prompt", "") or ""),
        "optimizationProfile": str(payload.get("optimization_profile", "balanced") or "balanced"),
        "networkMode": execution.get("networkMode"),
        "autoVerify": bool(payload.get("auto_verify", True)),
        "autoNetworkOptimize": bool(payload.get("auto_network_optimize", True)),
        "messages": messages,
        "assistantResponse": assistant_response or None,
        "toolTrace": execution.get("toolTrace") if isinstance(execution.get("toolTrace"), list) else [],
        "uav": sim_after,
        "replan": execution.get("replan"),
        "replanContext": execution.get("replanContext"),
        "utmVerify": execution.get("utmVerify"),
        "networkOptimization": execution.get("networkOptimization"),
        "networkState": network_state.get("result") if isinstance(network_state, dict) else None,
        "copilot": {
            "mode": "ollama_langgraph_planner",
            "llm": {
                "enabled": True,
                "provider": "ollama",
                "model": planner.get("model"),
                "plannerStatus": planner.get("status"),
                "summaryStatus": summary.get("status"),
            },
            "plan": {
                "actions": actions,
                "rawPlannerResponse": planner.get("raw"),
            },
        },
    }
    return {"result": result}


_copilot_builder = StateGraph(CopilotState)
_copilot_builder.add_node("plan", _plan_node)
_copilot_builder.add_node("execute", _execute_node)
_copilot_builder.add_node("summarize", _summary_node)
_copilot_builder.add_node("finalize", _finalize_node)
_copilot_builder.add_edge(START, "plan")
_copilot_builder.add_edge("plan", "execute")
_copilot_builder.add_edge("execute", "summarize")
_copilot_builder.add_edge("summarize", "finalize")
_copilot_builder.add_edge("finalize", END)
copilot_workflow = _copilot_builder.compile()


def run_copilot_workflow(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = copilot_workflow.invoke({"payload": dict(payload)})
    if not isinstance(state, dict):
        return {"status": "error", "error": "invalid_workflow_state"}
    planner = state.get("planner") if isinstance(state.get("planner"), dict) else {}
    result = state.get("result") if isinstance(state.get("result"), dict) else None
    return {
        "status": "success" if isinstance(result, dict) and planner.get("status") == "success" else "error",
        "planner": planner,
        "summary": state.get("summary") if isinstance(state.get("summary"), dict) else {},
        "result": result,
        "context_before": state.get("context_before") if isinstance(state.get("context_before"), dict) else {},
    }


__all__ = ["CopilotState", "copilot_workflow", "run_copilot_workflow"]
