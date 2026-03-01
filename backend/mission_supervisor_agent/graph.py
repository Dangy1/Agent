from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict, List

from langgraph.graph import END, START, StateGraph

from .command_bus import AuditableCommandBus
from .domain_dispatch import classify_command_operation_type, dispatch_domain_agent
from .lock_manager import LOCK_MANAGER
from .planner import build_intent, build_plan, classify_request, derive_mission_phase
from .policy import assess_risk, build_policy_decision_record, build_proposed_actions, find_missing_approvals, validate_policy
from .state import MissionState
from .watchers import ingest_events, refresh_network_state, refresh_uav_state, refresh_utm_state

COMMAND_BUS = AuditableCommandBus(dispatch_domain_agent)


def _last_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
    return ""


def ingest_request(state: MissionState) -> MissionState:
    text = str(state.get("request_text") or _last_user_text(state.get("messages")) or "")
    task_id = str(state.get("task_id") or f"mission-{uuid.uuid4().hex[:8]}")
    return {
        "task_id": task_id,
        "lock_owner": task_id,
        "request_text": text,
        "task_idempotency_key": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else task_id,
        "evidence_log": list(state.get("evidence_log") or []),
        "rollback_context": list(state.get("rollback_context") or []),
        "approvals": list(state.get("approvals") or []),
        "active_runs": dict(state.get("active_runs") or {}),
        "status": "ingested",
    }


def parse_intent(state: MissionState) -> MissionState:
    domain = classify_request(state.get("request_text", ""))
    return {"domain": domain, "intent": build_intent(state.get("request_text", ""), domain), "status": "intent_parsed"}


def risk_assessment(state: MissionState) -> MissionState:
    return {"risk_level": assess_risk(state.get("intent") or {}), "status": "risk_assessed"}


def plan_build(state: MissionState) -> MissionState:
    phase = derive_mission_phase(state)
    plan = build_plan(state)
    return {
        "mission_phase": phase,
        "plan": plan,
        "current_step": int(state.get("current_step", 0) or 0),
        "status": "planned",
    }


def approval_check(state: MissionState) -> MissionState:
    plan = state.get("plan") or []
    idx = int(state.get("current_step", 0) or 0)
    if idx >= len(plan):
        return {"approval_required": False, "pending_approvals": []}
    missing = find_missing_approvals(state, plan[idx])
    return {"approval_required": bool(missing), "pending_approvals": missing}


def approval_gate(state: MissionState) -> MissionState:
    evidence = list(state.get("evidence_log") or [])
    evidence.append({"node": "approval_gate", "pending": list(state.get("pending_approvals") or [])})
    return {"status": "awaiting_approval", "evidence_log": evidence}


def policy_check(state: MissionState) -> MissionState:
    proposed_actions = build_proposed_actions(state)
    notes = validate_policy(state)
    blocked = bool(notes)
    decision_log = list(state.get("decision_log") or [])
    decision_log.append(build_policy_decision_record(state, proposed_actions=proposed_actions, notes=notes))
    return {
        "proposed_actions": proposed_actions,
        "policy_notes": notes,
        "conflicts": notes,
        "decision_log": decision_log,
        "next_action": "rollback" if blocked else "continue",
        "status": "policy_checked",
    }


def lock_manager(state: MissionState) -> MissionState:
    plan = state.get("plan") or []
    idx = int(state.get("current_step", 0) or 0)
    if idx >= len(plan):
        return {"next_action": "complete"}
    step = plan[idx]
    keys = list(step.get("resource_keys") or [])
    owner = str(state.get("lock_owner") or "mission")
    ok, busy = LOCK_MANAGER.acquire_many(owner, keys)
    if not ok:
        return {"pending_lock_keys": busy, "execution_error": f"Lock busy: {', '.join(busy)}", "next_action": "rollback", "resource_locks": LOCK_MANAGER.snapshot()}
    return {"resource_locks": LOCK_MANAGER.snapshot(), "pending_lock_keys": [], "status": "locks_acquired"}


def dispatch_step(state: MissionState) -> MissionState:
    plan = state.get("plan") or []
    idx = int(state.get("current_step", 0) or 0)
    if idx >= len(plan):
        return {"next_action": "complete"}
    step = dict(plan[idx])
    return {"current_command": {"domain": step.get("domain"), "op": step.get("op"), "params": step.get("params") or {}, "step_id": step.get("step_id")}}


def execute_step(state: MissionState) -> MissionState:
    cmd = dict(state.get("current_command") or {})
    bus_output = COMMAND_BUS.execute(cmd, state)
    result = dict(bus_output.get("result") or {})
    audit = dict(bus_output.get("audit") or {})
    ev = list(state.get("evidence_log") or [])
    ev.append({"node": "execute_step", "command": cmd, "audit": audit, "result": result})
    bus_log = list(state.get("command_bus_log") or [])
    if audit:
        bus_log.append(audit)
    approvals = list(state.get("approvals") or [])
    if cmd.get("domain") == "utm" and result.get("status") == "success":
        rec = ((result.get("result") or {}) if isinstance(result.get("result"), dict) else {})
        if rec:
            approvals = [a for a in approvals if str(a.get("issuer")) != "utm"]
            approvals.append(
                {
                    "issuer": "utm",
                    "approved": bool(rec.get("approved", False)),
                    "signature_verified": bool(rec.get("signature_verified", False)),
                    "expires_at": rec.get("expires_at", ""),
                    "scope": rec.get("scope", {"uav_id": rec.get("uav_id"), "airspace": rec.get("airspace_segment")}),
                    "permissions": rec.get("permissions", []),
                }
            )
    rb_ctx = list(state.get("rollback_context") or [])
    applied_actions = list(state.get("applied_actions") or [])
    if cmd:
        applied_actions.append(
            {
                "ts": str(audit.get("responded_at") or (result.get("result") or {}).get("timestamp", "")),
                "phase": str(state.get("mission_phase") or "unknown"),
                "status": "applied" if result.get("status") == "success" else "failed",
                "operation_type": classify_command_operation_type(cmd),
                "domain": str(cmd.get("domain") or ""),
                "op": str(cmd.get("op") or ""),
                "step_id": str(cmd.get("step_id") or ""),
                "command_id": str(audit.get("command_id") or ""),
                "correlation_id": str(audit.get("correlation_id") or ""),
                "params": dict(cmd.get("params") or {}),
                "result": dict(result) if isinstance(result, dict) else {"raw": result},
            }
        )
    plan = state.get("plan") or []
    idx = int(state.get("current_step", 0) or 0)
    if idx < len(plan) and isinstance(plan[idx].get("rollback"), dict):
        rb_ctx.append({"command": plan[idx]["rollback"], "step_id": plan[idx].get("step_id")})
    return {
        "last_tool_result": result,
        "status": "executed",
        "evidence_log": ev,
        "rollback_context": rb_ctx,
        "approvals": approvals,
        "applied_actions": applied_actions,
        "command_bus_log": bus_log,
    }


def verify_outcome(state: MissionState) -> MissionState:
    ok = (state.get("last_tool_result") or {}).get("status") == "success"
    return {"next_action": "continue" if ok else "rollback", "status": "verified" if ok else "verify_failed"}


def progress(state: MissionState) -> MissionState:
    if state.get("next_action") == "rollback":
        return {"status": "rollback_required"}
    next_idx = int(state.get("current_step", 0) or 0) + 1
    done = next_idx >= len(state.get("plan") or [])
    return {"current_step": next_idx, "next_action": "complete" if done else "continue", "status": "progressed"}


def recovery(state: MissionState) -> MissionState:
    actions: List[Dict[str, Any]] = []
    for item in reversed(list(state.get("rollback_context") or [])[-2:]):
        cmd = item.get("command")
        if isinstance(cmd, dict):
            actions.append({"command": cmd, "result": dispatch_domain_agent(cmd, state)})
    ev = list(state.get("evidence_log") or [])
    ev.append({"node": "recovery", "actions": actions})
    return {"status": "rolled_back", "evidence_log": ev}


def release_locks(state: MissionState) -> MissionState:
    LOCK_MANAGER.release_owner(str(state.get("lock_owner") or "mission"))
    return {"resource_locks": LOCK_MANAGER.snapshot()}


def complete(state: MissionState) -> MissionState:
    return {"status": "completed"}


def _approval_branch(state: MissionState) -> str:
    return "approval_gate" if state.get("approval_required") else "policy_check"


def _policy_branch(state: MissionState) -> str:
    return "recovery" if state.get("next_action") == "rollback" else "lock_manager"


def _verify_branch(state: MissionState) -> str:
    return "recovery" if state.get("next_action") == "rollback" else "progress"


def _progress_branch(state: MissionState) -> str:
    return "complete" if state.get("next_action") == "complete" else "approval_check"


builder = StateGraph(MissionState)
for name, fn in [
    ("ingest_request", ingest_request),
    ("parse_intent", parse_intent),
    ("risk_assessment", risk_assessment),
    ("refresh_uav_state", refresh_uav_state),
    ("refresh_utm_state", refresh_utm_state),
    ("refresh_network_state", refresh_network_state),
    ("ingest_events", ingest_events),
    ("plan_build", plan_build),
    ("approval_check", approval_check),
    ("approval_gate", approval_gate),
    ("policy_check", policy_check),
    ("lock_manager", lock_manager),
    ("dispatch_step", dispatch_step),
    ("execute_step", execute_step),
    ("verify_outcome", verify_outcome),
    ("progress", progress),
    ("recovery", recovery),
    ("release_locks", release_locks),
    ("complete", complete),
]:
    builder.add_node(name, fn)

builder.add_edge(START, "ingest_request")
builder.add_edge("ingest_request", "parse_intent")
builder.add_edge("parse_intent", "risk_assessment")
builder.add_edge("risk_assessment", "refresh_uav_state")
builder.add_edge("refresh_uav_state", "refresh_utm_state")
builder.add_edge("refresh_utm_state", "refresh_network_state")
builder.add_edge("refresh_network_state", "ingest_events")
builder.add_edge("ingest_events", "plan_build")
builder.add_edge("plan_build", "approval_check")
builder.add_conditional_edges("approval_check", _approval_branch, {"approval_gate": "approval_gate", "policy_check": "policy_check"})
builder.add_edge("approval_gate", "release_locks")
builder.add_conditional_edges("policy_check", _policy_branch, {"recovery": "recovery", "lock_manager": "lock_manager"})
builder.add_edge("lock_manager", "dispatch_step")
builder.add_edge("dispatch_step", "execute_step")
builder.add_edge("execute_step", "verify_outcome")
builder.add_conditional_edges("verify_outcome", _verify_branch, {"recovery": "recovery", "progress": "progress"})
builder.add_conditional_edges("progress", _progress_branch, {"complete": "complete", "approval_check": "approval_check"})
builder.add_edge("recovery", "release_locks")
builder.add_edge("complete", "release_locks")
builder.add_edge("release_locks", END)

agent = builder.compile()

__all__ = ["agent"]
