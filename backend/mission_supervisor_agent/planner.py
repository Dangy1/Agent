from typing import Any, Dict, List

from .state import PlanStep


def classify_request(request_text: str) -> str:
    t = (request_text or "").lower()
    if any(k in t for k in ["uav", "drone", "flight", "utm"]):
        if any(k in t for k in ["slice", "tc", "kpm", "network"]):
            return "cross_domain"
        return "uav_mission"
    if "slice" in t:
        return "slice_ops"
    if "tc" in t:
        return "tc_ops"
    if "kpm" in t or "rc" in t:
        return "kpm_rc_ops"
    return "unknown"


def build_intent(request_text: str, domain: str) -> Dict[str, Any]:
    return {
        "goal": {
            "cross_domain": "coordinate_uav_network_mission",
            "uav_mission": "plan_uav_mission",
            "slice_ops": "manage_slice",
            "tc_ops": "manage_tc",
            "kpm_rc_ops": "manage_kpm_rc",
        }.get(domain, "generic_request"),
        "domain": domain,
        "raw_request": request_text,
        "constraints": {
            "approval_required": domain in {"uav_mission", "cross_domain"},
            "latency_ms_max": 20 if domain == "cross_domain" else None,
            "reliability_min": 0.999 if domain == "cross_domain" else None,
        },
    }


def _extract_context(state: Dict[str, Any]) -> Dict[str, Any]:
    mission = state.get("mission")
    metadata = mission.get("metadata") if isinstance(mission, dict) else {}
    intent = state.get("intent") or {}
    constraints = intent.get("constraints") if isinstance(intent, dict) else {}
    uav = state.get("uav_state_snapshot") or state.get("uav_state") or {}
    utm = state.get("utm_state_snapshot") or state.get("utm_state") or {}
    net = state.get("network_state_snapshot") or state.get("network_state") or {}
    mission_snap = state.get("mission_state_snapshot") or {}

    uav_id = str((metadata.get("uav_id") if isinstance(metadata, dict) else None) or uav.get("uav_id") or "uav-1")
    route_id = str(uav.get("route_id") or ("mission-route-1" if str(intent.get("domain")) == "cross_domain" else "uav-mission-1"))
    airspace_segment = str((metadata.get("airspace_segment") if isinstance(metadata, dict) else None) or utm.get("airspace_segment") or "sector-A3")
    flight_phase = str(uav.get("flight_phase") or "IDLE")
    battery_pct = float(uav.get("battery_pct", 100.0) or 100.0)
    utm_weather_ok = bool(((utm.get("weather_check") or {}).get("ok", True)) if isinstance(utm, dict) else True)
    utm_nfz_ok = bool(((utm.get("no_fly_zone_check") or {}).get("ok", True)) if isinstance(utm, dict) else True)
    utm_reg_ok = bool(((utm.get("regulation_check") or {}).get("ok", True)) if isinstance(utm, dict) else True)
    utm_license_ok = bool(((utm.get("license_check") or {}).get("ok", True)) if isinstance(utm, dict) else True)
    dss = utm.get("dss") if isinstance(utm, dict) and isinstance(utm.get("dss"), dict) else {}
    dss_blocking_conflict_count = int(dss.get("blocking_conflict_count", 0) or 0) if isinstance(dss, dict) else 0
    net_kpis = net.get("networkKpis") if isinstance(net, dict) else {}
    avg_latency_ms = float((net_kpis.get("avgLatencyMs", 0.0) if isinstance(net_kpis, dict) else 0.0) or 0.0)
    coverage_score = float((net_kpis.get("coverageScorePct", 100.0) if isinstance(net_kpis, dict) else 100.0) or 100.0)
    interference_risk_count = int((net_kpis.get("highInterferenceRiskCount", 0) if isinstance(net_kpis, dict) else 0) or 0)
    warnings = list(mission_snap.get("warnings") or []) if isinstance(mission_snap, dict) else []

    utm_approval = uav.get("utm_approval") if isinstance(uav, dict) else None
    approved = bool(isinstance(utm_approval, dict) and utm_approval.get("approved") and utm_approval.get("signature_verified"))
    uav_active = bool(uav.get("active")) if isinstance(uav, dict) else False
    uav_armed = bool(uav.get("armed")) if isinstance(uav, dict) else False
    latency_target = constraints.get("latency_ms_max") if isinstance(constraints, dict) else None

    return {
        "uav_id": uav_id,
        "route_id": route_id,
        "airspace_segment": airspace_segment,
        "flight_phase": flight_phase,
        "battery_pct": battery_pct,
        "utm_weather_ok": utm_weather_ok,
        "utm_nfz_ok": utm_nfz_ok,
        "utm_reg_ok": utm_reg_ok,
        "utm_license_ok": utm_license_ok,
        "utm_checks_ok": utm_weather_ok and utm_nfz_ok and utm_reg_ok and utm_license_ok,
        "dss_blocking_conflict_count": dss_blocking_conflict_count,
        "approved": approved,
        "uav_active": uav_active,
        "uav_armed": uav_armed,
        "coverage_score": coverage_score,
        "avg_latency_ms": avg_latency_ms,
        "interference_risk_count": interference_risk_count,
        "latency_target": float(latency_target) if isinstance(latency_target, (int, float)) else None,
        "warnings": warnings,
    }


def _derive_phase(state: Dict[str, Any], domain: str, ctx: Dict[str, Any]) -> str:
    explicit = str(state.get("mission_phase") or "").strip().lower()
    if explicit in {"mitigation", "closeout"}:
        return explicit
    if explicit in {"preflight", "launch", "execution", "mitigation", "closeout"}:
        phase = explicit
    else:
        phase = "preflight"

    if domain not in {"uav_mission", "cross_domain"}:
        return phase

    if ctx["flight_phase"] in {"ARRIVAL", "LAND", "LOITER"}:
        return "closeout"
    if ctx["flight_phase"] in {"HOLD", "LOW_BATTERY", "RTH"}:
        return "mitigation"
    if (not ctx["utm_checks_ok"]) or ("uav_low_battery" in ctx["warnings"]):
        return "mitigation"
    if ctx["dss_blocking_conflict_count"] > 0 or "utm_dss_blocking_conflicts" in ctx["warnings"]:
        return "mitigation"
    if "network_latency_high" in ctx["warnings"] or "network_interference_risk" in ctx["warnings"]:
        return "mitigation" if ctx["uav_active"] else "preflight"
    if ctx["uav_active"] or ctx["flight_phase"] in {"TAKEOFF", "MISSION"}:
        return "execution"
    if ctx["approved"] and (ctx["flight_phase"] in {"PLANNED", "IDLE"} or ctx["uav_armed"]):
        return "launch"
    return phase or "preflight"


def _network_profile_for_ctx(ctx: Dict[str, Any]) -> str:
    if ctx["interference_risk_count"] > 0:
        return "nvs-rate"
    if ctx["coverage_score"] < 88.0:
        return "static"
    return "nvs-rate"


def _plan_preflight(domain: str, ctx: Dict[str, Any]) -> List[PlanStep]:
    steps: List[PlanStep] = [
        {"step_id": "uav-plan", "domain": "uav", "op": "plan_route", "params": {"uav_id": ctx["uav_id"], "route_id": ctx["route_id"]}, "resource_keys": [f"uav:{ctx['uav_id']}:flight_control"]},
        {"step_id": "uav-geofence", "domain": "uav", "op": "submit_route_geofence", "params": {"uav_id": ctx["uav_id"], "airspace_segment": ctx["airspace_segment"]}, "resource_keys": [f"utm:{ctx['airspace_segment']}"]},
        {
            "step_id": "utm-verify",
            "domain": "utm",
            "op": "verify_flight_plan",
            "params": {"uav_id": ctx["uav_id"], "airspace_segment": ctx["airspace_segment"], "route_id": ctx["route_id"]},
            "requires_approvals": ["utm"],
            "resource_keys": [f"utm:{ctx['airspace_segment']}", f"uav:{ctx['uav_id']}:flight_control"],
        },
    ]
    if domain == "cross_domain":
        steps.extend(
            [
                {"step_id": "network-health", "domain": "network", "op": "health", "params": {}, "resource_keys": []},
                {
                    "step_id": "slice-apply",
                    "domain": "network",
                    "op": "slice_apply_profile",
                    "params": {"profile": _network_profile_for_ctx(ctx), "duration_s": 60},
                    "resource_keys": ["ran:slice"],
                },
                {"step_id": "kpm-monitor", "domain": "network", "op": "kpm_monitor", "params": {"duration_s": 20}, "resource_keys": ["telemetry:kpm_rc"]},
            ]
        )
    return steps


def _plan_launch(domain: str, ctx: Dict[str, Any]) -> List[PlanStep]:
    steps: List[PlanStep] = []
    if domain == "cross_domain":
        steps.extend(
            [
                {
                    "step_id": "slice-apply-launch",
                    "domain": "network",
                    "op": "slice_apply_profile",
                    "params": {"profile": _network_profile_for_ctx(ctx), "duration_s": 45},
                    "resource_keys": ["ran:slice"],
                },
                {"step_id": "kpm-monitor-launch", "domain": "network", "op": "kpm_monitor", "params": {"duration_s": 10}, "resource_keys": ["telemetry:kpm_rc"]},
            ]
        )
    steps.append(
        {
            "step_id": "uav-launch",
            "domain": "uav",
            "op": "launch",
            "params": {"uav_id": ctx["uav_id"]},
            "requires_approvals": ["utm"],
            "resource_keys": [f"uav:{ctx['uav_id']}:flight_control"],
            "rollback": {"domain": "uav", "op": "rth", "params": {"uav_id": ctx["uav_id"]}},
        }
    )
    steps.append({"step_id": "uav-status-post-launch", "domain": "uav", "op": "status", "params": {"uav_id": ctx["uav_id"]}, "resource_keys": []})
    return steps


def _plan_execution(domain: str, ctx: Dict[str, Any]) -> List[PlanStep]:
    steps: List[PlanStep] = []
    if domain == "cross_domain":
        steps.extend(
            [
                {"step_id": "kpm-monitor", "domain": "network", "op": "kpm_monitor", "params": {"duration_s": 15}, "resource_keys": ["telemetry:kpm_rc"]},
                {"step_id": "network-health", "domain": "network", "op": "health", "params": {}, "resource_keys": []},
            ]
        )
        if (ctx["latency_target"] is not None and ctx["avg_latency_ms"] > ctx["latency_target"]) or ctx["interference_risk_count"] > 0:
            steps.append(
                {
                    "step_id": "slice-tune-execution",
                    "domain": "network",
                    "op": "slice_apply_profile",
                    "params": {"profile": "nvs-rate", "duration_s": 45},
                    "resource_keys": ["ran:slice"],
                }
            )
    ticks = 1 if ctx["battery_pct"] < 30.0 else 2
    steps.extend(
        [
            {"step_id": "uav-step", "domain": "uav", "op": "sim_step", "params": {"uav_id": ctx["uav_id"], "ticks": ticks}, "resource_keys": [f"uav:{ctx['uav_id']}:flight_control"]},
            {"step_id": "uav-status", "domain": "uav", "op": "status", "params": {"uav_id": ctx["uav_id"]}, "resource_keys": []},
        ]
    )
    return steps


def _plan_mitigation(domain: str, ctx: Dict[str, Any]) -> List[PlanStep]:
    steps: List[PlanStep] = [
        {
            "step_id": "uav-hold",
            "domain": "uav",
            "op": "hold",
            "params": {"uav_id": ctx["uav_id"], "reason": "supervisor_mitigation"},
            "resource_keys": [f"uav:{ctx['uav_id']}:flight_control"],
        },
        {"step_id": "uav-status-mitigation", "domain": "uav", "op": "status", "params": {"uav_id": ctx["uav_id"]}, "resource_keys": []},
    ]
    if domain == "cross_domain":
        steps[0:0] = [
            {"step_id": "network-health-mitigation", "domain": "network", "op": "health", "params": {}, "resource_keys": []},
            {"step_id": "kpm-monitor-mitigation", "domain": "network", "op": "kpm_monitor", "params": {"duration_s": 10}, "resource_keys": ["telemetry:kpm_rc"]},
        ]
        if ctx["coverage_score"] < 85.0 or ctx["interference_risk_count"] > 0:
            steps.insert(
                2,
                {
                    "step_id": "slice-apply-mitigation",
                    "domain": "network",
                    "op": "slice_apply_profile",
                    "params": {"profile": "static", "duration_s": 45},
                    "resource_keys": ["ran:slice"],
                },
            )
    if ctx["battery_pct"] < 15.0:
        steps.append(
            {
                "step_id": "uav-rth",
                "domain": "uav",
                "op": "rth",
                "params": {"uav_id": ctx["uav_id"]},
                "resource_keys": [f"uav:{ctx['uav_id']}:flight_control"],
            }
        )
    return steps


def _plan_closeout(domain: str, ctx: Dict[str, Any]) -> List[PlanStep]:
    steps: List[PlanStep] = []
    if ctx["flight_phase"] not in {"LAND"}:
        steps.append(
            {
                "step_id": "uav-land",
                "domain": "uav",
                "op": "land",
                "params": {"uav_id": ctx["uav_id"]},
                "resource_keys": [f"uav:{ctx['uav_id']}:flight_control"],
            }
        )
    steps.append({"step_id": "uav-status-closeout", "domain": "uav", "op": "status", "params": {"uav_id": ctx["uav_id"]}, "resource_keys": []})
    if domain == "cross_domain":
        steps.append({"step_id": "network-health-closeout", "domain": "network", "op": "health", "params": {}, "resource_keys": []})
    return steps


def _build_phase_plan(state: Dict[str, Any]) -> List[PlanStep]:
    intent = state.get("intent") or {}
    domain = str(intent.get("domain", "unknown"))
    ctx = _extract_context(state)
    phase = _derive_phase(state, domain, ctx)

    if domain == "cross_domain":
        if phase == "mitigation":
            return _plan_mitigation(domain, ctx)
        if phase == "closeout":
            return _plan_closeout(domain, ctx)
        if phase == "execution":
            return _plan_execution(domain, ctx)
        if phase == "launch":
            return _plan_launch(domain, ctx)
        return _plan_preflight(domain, ctx)

    if domain == "uav_mission":
        if phase == "mitigation":
            return _plan_mitigation(domain, ctx)
        if phase == "closeout":
            return _plan_closeout(domain, ctx)
        if phase == "execution":
            return _plan_execution(domain, ctx)
        if phase == "launch":
            return _plan_launch(domain, ctx)
        return _plan_preflight(domain, ctx)

    if domain == "slice_ops":
        return [{"step_id": "slice-apply", "domain": "network", "op": "slice_apply_profile", "params": {"profile": "static", "duration_s": 30}, "resource_keys": ["ran:slice"]}]
    if domain == "tc_ops":
        return [{"step_id": "tc-start", "domain": "network", "op": "tc_start", "params": {"profile": "default", "duration_s": 60}, "resource_keys": ["net:tc"]}]
    if domain == "kpm_rc_ops":
        return [{"step_id": "kpm-monitor", "domain": "network", "op": "kpm_monitor", "params": {"duration_s": 20}, "resource_keys": ["telemetry:kpm_rc"]}]
    return [{"step_id": "health", "domain": "network", "op": "health", "params": {}, "resource_keys": []}]


def derive_mission_phase(state: Dict[str, Any]) -> str:
    intent = state.get("intent") or {}
    domain = str(intent.get("domain", "unknown"))
    return _derive_phase(state, domain, _extract_context(state))


def build_plan(state_or_intent: Dict[str, Any]) -> List[PlanStep]:
    # Backward-compatible fallback: if only an intent dict is passed, synthesize minimal state.
    if "intent" not in state_or_intent and "domain" in state_or_intent:
        return _build_phase_plan({"intent": state_or_intent})
    return _build_phase_plan(state_or_intent)
