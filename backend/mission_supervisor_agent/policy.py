from datetime import datetime, timezone
from typing import Any, Dict, List

from .command_types import classify_command_operation_type
from .state import MissionState, PlanStep


def assess_risk(intent: Dict[str, Any]) -> str:
    text = f"{intent.get('goal','')} {intent.get('domain','')}".lower()
    if "uav" in text or "cross" in text:
        return "high"
    if any(k in text for k in ["slice", "tc", "kpm"]):
        return "medium"
    return "low"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _current_step(state: MissionState) -> Dict[str, Any]:
    plan = state.get("plan") or []
    idx = int(state.get("current_step", 0) or 0)
    if idx < 0 or idx >= len(plan):
        return {}
    step = plan[idx]
    return dict(step) if isinstance(step, dict) else {}


def build_proposed_actions(state: MissionState) -> List[Dict[str, Any]]:
    step = _current_step(state)
    if not step:
        return []
    cmd_base = {
        "ts": _now_iso(),
        "phase": str(state.get("mission_phase") or "unknown"),
        "status": "proposed",
        "domain": str(step.get("domain") or ""),
        "op": str(step.get("op") or ""),
        "step_id": str(step.get("step_id") or ""),
        "params": dict(step.get("params") or {}),
        "reason": "current_plan_step",
    }
    cmd = dict(cmd_base)
    cmd["operation_type"] = classify_command_operation_type(cmd_base)
    return [cmd]


def _latest_proposed_action(state: MissionState) -> Dict[str, Any]:
    proposed = state.get("proposed_actions") or []
    if proposed:
        last = proposed[-1]
        return dict(last) if isinstance(last, dict) else {}
    derived = build_proposed_actions(state)
    return dict(derived[-1]) if derived else {}


def _jurisdiction_profile_id(state: MissionState) -> str:
    mission = state.get("mission")
    metadata = mission.get("metadata") if isinstance(mission, dict) else {}
    if isinstance(metadata, dict) and str(metadata.get("jurisdiction_profile", "")).strip():
        return str(metadata.get("jurisdiction_profile")).strip().lower()
    raw = state.get("metadata")
    if isinstance(raw, dict) and str(raw.get("jurisdiction_profile", "")).strip():
        return str(raw.get("jurisdiction_profile")).strip().lower()
    return ""


def _operation_type(action: Dict[str, Any]) -> str:
    op_type = str(action.get("operation_type") or "")
    if op_type in {"observe", "actuate", "unknown"}:
        return op_type
    return classify_command_operation_type(action)


def _phase_allows_action(phase: str, domain: str, op: str) -> bool:
    observe_ops = {
        ("uav", "status"),
        ("utm", "verify_flight_plan"),
        ("utm", "check_geofence"),
        ("utm", "weather_check"),
        ("utm", "no_fly_zone_check"),
        ("utm", "regulation_check"),
        ("utm", "time_window_check"),
        ("utm", "operator_license_check"),
        ("utm", "query_operational_intents"),
        ("utm", "dss_query_operational_intents"),
        ("utm", "query_subscriptions"),
        ("utm", "dss_query_subscriptions"),
        ("utm", "query_participants"),
        ("utm", "dss_query_participants"),
        ("utm", "query_notifications"),
        ("utm", "dss_query_notifications"),
        ("utm", "conformance_last"),
        ("utm", "dss_conformance_last"),
        ("network", "health"),
        ("network", "kpm_monitor"),
        ("dss", "state"),
        ("dss", "query_operational_intents"),
        ("dss", "dss_query_operational_intents"),
        ("dss", "query_subscriptions"),
        ("dss", "dss_query_subscriptions"),
        ("dss", "query_participants"),
        ("dss", "dss_query_participants"),
        ("dss", "query_notifications"),
        ("dss", "dss_query_notifications"),
        ("dss", "conformance_last"),
        ("dss", "dss_conformance_last"),
        ("uss", "state"),
        ("uss", "query_peer_intents"),
        ("uss", "query_operational_intents"),
        ("uss", "uss_query_intents"),
        ("uss", "pull_notifications"),
        ("uss", "uss_pull_notifications"),
    }
    if (domain, op) in observe_ops:
        return True

    allowed_by_phase = {
        "preflight": {
            ("uav", "plan_route"),
            ("uav", "submit_route_geofence"),
            ("utm", "verify_flight_plan"),
            ("network", "slice_apply_profile"),
            ("network", "tc_start"),
            ("network", "kpm_start"),
            ("dss", "upsert_operational_intent"),
            ("dss", "dss_upsert_operational_intent"),
            ("dss", "upsert_subscription"),
            ("dss", "dss_upsert_subscription"),
            ("dss", "upsert_participant"),
            ("dss", "dss_upsert_participant"),
            ("dss", "run_local_conformance"),
            ("dss", "dss_run_local_conformance"),
            ("uss", "publish_intent"),
            ("uss", "uss_publish_intent"),
            ("uss", "upsert_operational_intent"),
            ("uss", "subscribe_airspace"),
            ("uss", "uss_subscribe_airspace"),
            ("uss", "upsert_subscription"),
            ("uss", "register_participant"),
            ("uss", "uss_register_participant"),
        },
        "launch": {
            ("uav", "launch"),
            ("uav", "status"),
            ("network", "slice_apply_profile"),
            ("network", "kpm_monitor"),
            ("network", "health"),
        },
        "execution": {
            ("uav", "sim_step"),
            ("uav", "status"),
            ("uav", "hold"),
            ("uav", "rth"),
            ("network", "health"),
            ("network", "kpm_monitor"),
            ("network", "slice_apply_profile"),
            ("dss", "upsert_operational_intent"),
            ("dss", "dss_upsert_operational_intent"),
            ("dss", "delete_operational_intent"),
            ("dss", "dss_delete_operational_intent"),
            ("dss", "upsert_subscription"),
            ("dss", "dss_upsert_subscription"),
            ("dss", "delete_subscription"),
            ("dss", "dss_delete_subscription"),
            ("dss", "ack_notification"),
            ("dss", "dss_ack_notification"),
            ("uss", "publish_intent"),
            ("uss", "uss_publish_intent"),
            ("uss", "upsert_operational_intent"),
            ("uss", "subscribe_airspace"),
            ("uss", "uss_subscribe_airspace"),
            ("uss", "upsert_subscription"),
            ("uss", "ack_notification"),
            ("uss", "uss_ack_notification"),
        },
        "mitigation": {
            ("uav", "hold"),
            ("uav", "rth"),
            ("uav", "land"),
            ("uav", "status"),
            ("network", "health"),
            ("network", "kpm_monitor"),
            ("network", "slice_apply_profile"),
            ("dss", "upsert_operational_intent"),
            ("dss", "dss_upsert_operational_intent"),
            ("dss", "delete_operational_intent"),
            ("dss", "dss_delete_operational_intent"),
            ("dss", "upsert_subscription"),
            ("dss", "dss_upsert_subscription"),
            ("dss", "delete_subscription"),
            ("dss", "dss_delete_subscription"),
            ("dss", "ack_notification"),
            ("dss", "dss_ack_notification"),
            ("dss", "run_local_conformance"),
            ("dss", "dss_run_local_conformance"),
            ("uss", "publish_intent"),
            ("uss", "uss_publish_intent"),
            ("uss", "upsert_operational_intent"),
            ("uss", "subscribe_airspace"),
            ("uss", "uss_subscribe_airspace"),
            ("uss", "upsert_subscription"),
            ("uss", "ack_notification"),
            ("uss", "uss_ack_notification"),
        },
        "closeout": {
            ("uav", "land"),
            ("uav", "status"),
            ("network", "health"),
            ("network", "kpm_monitor"),
            ("dss", "query_notifications"),
            ("dss", "dss_query_notifications"),
            ("dss", "ack_notification"),
            ("dss", "dss_ack_notification"),
            ("uss", "pull_notifications"),
            ("uss", "uss_pull_notifications"),
            ("uss", "ack_notification"),
            ("uss", "uss_ack_notification"),
        },
    }
    return (domain, op) in allowed_by_phase.get(phase, set())


def _utm_checks_ok(state: MissionState) -> bool:
    utm = state.get("utm_state_snapshot") or state.get("utm_state") or {}
    if not isinstance(utm, dict):
        return True
    for key in ("weather_check", "no_fly_zone_check", "regulation_check", "license_check"):
        check = utm.get(key)
        if isinstance(check, dict) and check.get("ok") is False:
            return False
    return True


def _valid_utm_approval_present(state: MissionState) -> bool:
    approvals = state.get("approvals") or []
    for approval in approvals:
        if isinstance(approval, dict) and str(approval.get("issuer")) == "utm" and approval_valid(approval):
            return True
    uav = state.get("uav_state_snapshot") or state.get("uav_state") or {}
    if isinstance(uav, dict) and isinstance(uav.get("utm_approval"), dict):
        return approval_valid(dict(uav["utm_approval"]))  # type: ignore[index]
    return False


def _append_snapshot_guardrails(state: MissionState, action: Dict[str, Any], notes: List[str]) -> None:
    domain = str(action.get("domain") or "")
    op = str(action.get("op") or "")
    op_type = _operation_type(action)
    phase = str(state.get("mission_phase") or "unknown")
    uav = state.get("uav_state_snapshot") or state.get("uav_state") or {}
    net = state.get("network_state_snapshot") or state.get("network_state") or {}
    utm = state.get("utm_state_snapshot") or state.get("utm_state") or {}
    mission_snap = state.get("mission_state_snapshot") or {}
    params = dict(action.get("params") or {})
    warnings = list(mission_snap.get("warnings") or []) if isinstance(mission_snap, dict) else []

    # Observe path: always allow common telemetry/state reads across phases.
    # Keep only light sanity checks for malformed/unsafe pseudo-observe requests.
    if op_type == "observe":
        always_allow_observe = {
            ("uav", "status"),
            ("utm", "check_geofence"),
            ("utm", "weather_check"),
            ("utm", "no_fly_zone_check"),
            ("utm", "regulation_check"),
            ("utm", "time_window_check"),
            ("utm", "operator_license_check"),
            ("utm", "query_operational_intents"),
            ("utm", "dss_query_operational_intents"),
            ("utm", "query_subscriptions"),
            ("utm", "dss_query_subscriptions"),
            ("utm", "query_participants"),
            ("utm", "dss_query_participants"),
            ("utm", "query_notifications"),
            ("utm", "dss_query_notifications"),
            ("utm", "conformance_last"),
            ("utm", "dss_conformance_last"),
            ("network", "health"),
            ("network", "slice_monitor"),
            ("network", "kpm_monitor"),
            ("dss", "state"),
            ("dss", "query_operational_intents"),
            ("dss", "dss_query_operational_intents"),
            ("dss", "query_subscriptions"),
            ("dss", "dss_query_subscriptions"),
            ("dss", "query_participants"),
            ("dss", "dss_query_participants"),
            ("dss", "query_notifications"),
            ("dss", "dss_query_notifications"),
            ("dss", "conformance_last"),
            ("dss", "dss_conformance_last"),
            ("uss", "state"),
            ("uss", "query_peer_intents"),
            ("uss", "query_operational_intents"),
            ("uss", "uss_query_intents"),
            ("uss", "pull_notifications"),
            ("uss", "uss_pull_notifications"),
        }
        if (domain, op) in always_allow_observe:
            return
        if (domain, op) == ("utm", "verify_flight_plan"):
            if isinstance(uav, dict) and int(uav.get("waypoints_total", 0) or 0) <= 0 and not uav.get("waypoints"):
                notes.append("utm_verify_blocked_missing_waypoints")
            return
        # Unknown observe classification should not silently pass.
        notes.append(f"observe_command_not_allowlisted:{domain}.{op}")
        return

    if op_type != "actuate":
        notes.append(f"action_blocked_unknown_operation_type:{domain}.{op}")
        return

    if not _phase_allows_action(phase, domain, op):
        notes.append(f"action_blocked_phase_mismatch:{phase}:{domain}.{op}")

    if phase in {"", "unknown"}:
        notes.append("actuation_blocked_missing_mission_phase")

    if domain == "uav" and op not in {"plan_route"} and (not isinstance(uav, dict) or not uav):
        notes.append(f"uav_actuation_blocked_missing_uav_snapshot:{op}")
    if domain == "network" and op in {"slice_apply_profile", "tc_start", "kpm_start"} and (not isinstance(net, dict) or not net):
        notes.append(f"network_actuation_blocked_missing_network_snapshot:{op}")
    if domain == "utm" and op in {"set_weather", "reserve_corridor"} and (not isinstance(utm, dict) or not utm):
        notes.append(f"utm_actuation_blocked_missing_utm_snapshot:{op}")

    if "network_interference_risk" in warnings and domain == "uav" and op == "launch":
        notes.append("uav_launch_blocked_network_interference_risk")

    if domain == "uav" and op == "launch":
        if not _utm_checks_ok(state):
            notes.append("uav_launch_blocked_utm_checks_failed")
        if not _valid_utm_approval_present(state):
            notes.append("uav_launch_blocked_missing_valid_utm_approval")
        battery = float(uav.get("battery_pct", 100.0) or 100.0) if isinstance(uav, dict) else 100.0
        if battery < 25.0:
            notes.append("uav_launch_blocked_low_battery")
        flight_phase = str(uav.get("flight_phase") or "") if isinstance(uav, dict) else ""
        if flight_phase and flight_phase not in {"IDLE", "PLANNED", "HOLD"}:
            notes.append(f"uav_launch_blocked_invalid_flight_phase:{flight_phase}")

    if domain == "uav" and op == "sim_step":
        if not isinstance(uav, dict) or not uav:
            notes.append("uav_sim_step_blocked_missing_uav_snapshot")
        battery = float(uav.get("battery_pct", 100.0) or 100.0) if isinstance(uav, dict) else 100.0
        if battery < 10.0:
            notes.append("uav_sim_step_blocked_critical_battery")
        if "uav_low_battery" in warnings:
            notes.append("uav_sim_step_blocked_low_battery_warning")

    if domain == "uav" and op == "plan_route":
        if phase not in {"preflight", "mitigation"}:
            notes.append(f"uav_plan_route_blocked_in_phase:{phase}")

    if domain == "utm" and op == "verify_flight_plan":
        if isinstance(uav, dict) and int(uav.get("waypoints_total", 0) or 0) <= 0 and not uav.get("waypoints"):
            notes.append("utm_verify_blocked_missing_waypoints")

    if domain == "network" and op == "slice_apply_profile":
        profile = str(params.get("profile") or "")
        if profile not in {"static", "monitor", "nvs-rate", "nvs-cap", "edf"}:
            notes.append(f"network_slice_apply_blocked_profile_not_allowed:{profile}")
        kpis = net.get("networkKpis") if isinstance(net, dict) else {}
        avg_latency = float(kpis.get("avgLatencyMs", 0.0) or 0.0) if isinstance(kpis, dict) else 0.0
        if phase == "closeout":
            notes.append("network_slice_apply_blocked_during_closeout")
        if phase == "preflight" and avg_latency > 60.0:
            notes.append("network_slice_apply_blocked_preflight_unstable_network")

    if domain == "dss" and op in {"upsert_operational_intent", "dss_upsert_operational_intent"}:
        volume4d = params.get("volume4d")
        if not isinstance(volume4d, dict) or not volume4d:
            notes.append("dss_upsert_intent_blocked_missing_volume4d")

    if domain == "uss" and op in {"publish_intent", "upsert_operational_intent", "uss_publish_intent"}:
        manager_uss_id = str(params.get("manager_uss_id") or "").strip()
        if not manager_uss_id:
            notes.append("uss_publish_intent_blocked_missing_manager_uss_id")


def _append_profile_guardrails(state: MissionState, action: Dict[str, Any], notes: List[str]) -> None:
    profile_id = _jurisdiction_profile_id(state)
    if not profile_id:
        return
    domain = str(action.get("domain") or "")
    op = str(action.get("op") or "")
    if (domain, op) != ("uav", "launch"):
        return
    utm = state.get("utm_state_snapshot") or state.get("utm_state") or {}
    uav = state.get("uav_state_snapshot") or state.get("uav_state") or {}
    dss = utm.get("dss") if isinstance(utm, dict) and isinstance(utm.get("dss"), dict) else {}
    dss_result = uav.get("utm_dss_result") if isinstance(uav, dict) and isinstance(uav.get("utm_dss_result"), dict) else {}

    if profile_id.startswith("eu_ussp"):
        if bool(dss.get("subscription_stale")):
            notes.append("eu_ussp_launch_blocked_dss_subscription_stale")
        if float(dss.get("pending_notification_lag_sec_max", 0.0) or 0.0) > 180.0:
            notes.append("eu_ussp_launch_blocked_dss_notification_lag")
        if str(dss_result.get("status", "")).strip().lower() == "error" or str(dss_result.get("error", "")).strip():
            notes.append("eu_ussp_launch_blocked_dss_publish_error")
        if bool(dss_result.get("degraded")):
            notes.append("eu_ussp_launch_blocked_degraded_dss_mode")
    elif profile_id.startswith("us_faa"):
        if int(dss.get("blocking_conflict_count", 0) or 0) > 0:
            notes.append("us_faa_launch_blocked_dss_blocking_conflicts")


def build_policy_decision_record(
    state: MissionState,
    *,
    proposed_actions: List[Dict[str, Any]],
    notes: List[str],
) -> Dict[str, Any]:
    return {
        "ts": _now_iso(),
        "node": "policy_check",
        "decision": "block" if notes else "allow",
        "reason": ";".join(notes) if notes else "guardrails_passed",
        "inputs": {
            "mission_phase": state.get("mission_phase"),
            "current_step": state.get("current_step"),
            "proposed_actions": proposed_actions,
        },
        "outputs": {
            "policy_notes": notes,
        },
    }


def validate_policy(state: MissionState) -> List[str]:
    notes: List[str] = []
    step = _current_step(state)
    action = _latest_proposed_action(state)
    op_type = _operation_type(action) if action else "unknown"
    active_runs = state.get("active_runs") or {}
    if op_type == "actuate" and step.get("domain") in {"slice", "network"} and str(step.get("op")) == "slice_apply_profile" and active_runs.get("slice"):
        notes.append("slice_modify_conflict_active_slice_run")
    uav = state.get("uav_state_snapshot") or state.get("uav_state") or {}
    flight_phase = str((uav.get("flight_phase") if isinstance(uav, dict) else "") or "").upper()
    if op_type == "actuate" and step.get("domain") in {"tc", "network"} and str(step.get("op")) == "tc_start" and flight_phase in {"TAKEOFF", "LANDING"}:
        notes.append("tc_changes_blocked_during_critical_flight_phase")
    emergency_ops = {("uav", "hold"), ("uav", "rth"), ("uav", "land")}
    step_key = (str(step.get("domain") or ""), str(step.get("op") or ""))
    if (
        op_type == "actuate"
        and step_key not in emergency_ops
        and step.get("domain") in {"uav", "utm", "network"}
        and state.get("risk_level") == "high"
        and not (state.get("approvals") or [])
    ):
        notes.append("high_risk_requires_approvals")
    if action:
        _append_snapshot_guardrails(state, action, notes)
        _append_profile_guardrails(state, action, notes)
    return notes


def approval_valid(approval: Dict[str, Any]) -> bool:
    if approval.get("approved") is not True:
        return False
    if approval.get("signature_verified") is False:
        return False
    expires_at = approval.get("expires_at")
    if isinstance(expires_at, str) and expires_at:
        try:
            dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if dt < datetime.now(timezone.utc):
                return False
        except Exception:
            return False
    return True


def find_missing_approvals(state: MissionState, step: PlanStep) -> List[str]:
    needed = [str(x) for x in (step.get("requires_approvals") or [])]
    if not needed:
        return []
    valid = {str(a.get("issuer")) for a in (state.get("approvals") or []) if approval_valid(a)}
    return [x for x in needed if x not in valid]
