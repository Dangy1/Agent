from typing import Any, Dict

from network_agent import tools as net_tools
from uav_agent import tools as uav_tools
from utm_agent import tools as utm_tools

from .command_types import OBSERVE_OPS, classify_command_operation_type


def dispatch_observe_command(command: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    domain = str(command.get("domain", ""))
    op = str(command.get("op", ""))
    params = dict(command.get("params") or {})

    if domain == "uav":
        if op == "status":
            return uav_tools.uav_status.invoke(params)

    if domain == "utm":
        if op == "verify_flight_plan":
            return utm_tools.utm_verify_flight_plan.invoke(params)
        if op == "check_geofence":
            return utm_tools.utm_check_geofence.invoke(params)
        if op == "weather_check":
            return utm_tools.utm_weather_check.invoke(params)
        if op == "no_fly_zone_check":
            return utm_tools.utm_no_fly_zone_check.invoke(params)
        if op == "regulation_check":
            return utm_tools.utm_regulation_check.invoke(params)
        if op == "time_window_check":
            return utm_tools.utm_time_window_check.invoke(params)
        if op == "operator_license_check":
            return utm_tools.utm_operator_license_check.invoke(params)
        if op in {"query_operational_intents", "dss_query_operational_intents"}:
            return utm_tools.utm_dss_query_operational_intents.invoke(params)
        if op in {"query_subscriptions", "dss_query_subscriptions"}:
            return utm_tools.utm_dss_query_subscriptions.invoke(params)
        if op in {"query_participants", "dss_query_participants"}:
            return utm_tools.utm_dss_query_participants.invoke(params)
        if op in {"query_notifications", "dss_query_notifications"}:
            return utm_tools.utm_dss_query_notifications.invoke(params)
        if op in {"conformance_last", "dss_conformance_last"}:
            return utm_tools.utm_dss_get_last_conformance.invoke(params)

    if domain == "network":
        if op == "health":
            return net_tools.mcp_health.invoke({})
        if op == "slice_monitor":
            return net_tools.mcp_slice_monitor_check.invoke(
                {"duration_s": int(params.get("duration_s", 20)), "verbose": bool(params.get("verbose", False)), "stop_after_check": True}
            )
        if op == "kpm_monitor":
            return net_tools.mcp_kpm_monitor_check.invoke(
                {"period_ms": int(params.get("period_ms", 1000)), "duration_s": int(params.get("duration_s", 20)), "kpm_metrics": params.get("kpm_metrics", "rru"), "stop_after_check": True}
            )

    return {"status": "error", "agent": "dispatcher", "error": f"Unsupported observe command {domain}.{op}"}


def dispatch_actuate_command(command: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    domain = str(command.get("domain", ""))
    op = str(command.get("op", ""))
    params = dict(command.get("params") or {})

    if domain == "uav":
        if op == "plan_route":
            return uav_tools.uav_plan_route.invoke(params)
        if op in {"submit_route_geofence", "submit_route_to_utm_geofence_check"}:
            return uav_tools.uav_submit_route_to_utm_geofence_check.invoke(params)
        if op in {"request_utm_approval", "utm_approval"}:
            return uav_tools.uav_request_utm_approval.invoke(params)
        if op == "launch":
            return uav_tools.uav_launch.invoke(params)
        if op in {"step", "sim_step"}:
            return uav_tools.uav_sim_step.invoke(params)
        if op == "hold":
            return uav_tools.uav_hold.invoke(params)
        if op in {"rth", "return_to_home"}:
            return uav_tools.uav_return_to_home.invoke(params)
        if op == "land":
            return uav_tools.uav_land.invoke(params)

    if domain == "utm":
        if op == "reserve_corridor":
            return utm_tools.utm_reserve_corridor.invoke(params)
        if op == "set_weather":
            return utm_tools.utm_set_weather.invoke(params)
        if op in {"upsert_operational_intent", "dss_upsert_operational_intent"}:
            return utm_tools.utm_dss_upsert_operational_intent.invoke(params)
        if op in {"delete_operational_intent", "dss_delete_operational_intent"}:
            return utm_tools.utm_dss_delete_operational_intent.invoke(params)
        if op in {"upsert_subscription", "dss_upsert_subscription"}:
            return utm_tools.utm_dss_upsert_subscription.invoke(params)
        if op in {"delete_subscription", "dss_delete_subscription"}:
            return utm_tools.utm_dss_delete_subscription.invoke(params)
        if op in {"upsert_participant", "dss_upsert_participant"}:
            return utm_tools.utm_dss_upsert_participant.invoke(params)
        if op in {"delete_participant", "dss_delete_participant"}:
            return utm_tools.utm_dss_delete_participant.invoke(params)
        if op in {"ack_notification", "dss_ack_notification"}:
            return utm_tools.utm_dss_ack_notification.invoke(params)
        if op in {"run_local_conformance", "dss_run_local_conformance"}:
            return utm_tools.utm_dss_run_local_conformance.invoke(params)

    if domain == "network":
        if op == "slice_apply_profile":
            return net_tools.mcp_slice_apply_profile_and_verify.invoke(
                {
                    "profile": params.get("profile", "static"),
                    "duration_s": int(params.get("duration_s", 60)),
                    "verbose": bool(params.get("verbose", True)),
                    "stop_after_verify": bool(params.get("stop_after_verify", True)),
                }
            )
        if op == "tc_start":
            return net_tools.mcp_tc_start.invoke(
                {"profile": params.get("profile", "default"), "duration_s": int(params.get("duration_s", 60)), "monitor_rlc": False}
            )
        if op == "kpm_start":
            return net_tools.mcp_kpm_rc_start.invoke(
                {"profile": params.get("profile", "kpm"), "period_ms": int(params.get("period_ms", 1000)), "duration_s": int(params.get("duration_s", 60)), "kpm_metrics": params.get("kpm_metrics", "rru")}
            )

    return {"status": "error", "agent": "dispatcher", "error": f"Unsupported actuate command {domain}.{op}"}


def dispatch_domain_agent(command: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    domain = str(command.get("domain", ""))
    op = str(command.get("op", ""))
    op_type = classify_command_operation_type(command)
    if op_type == "observe":
        return dispatch_observe_command(command, state)
    if op_type == "actuate":
        return dispatch_actuate_command(command, state)
    return {"status": "error", "agent": "dispatcher", "error": f"Unsupported command {domain}.{op}"}


__all__ = [
    "OBSERVE_OPS",
    "classify_command_operation_type",
    "dispatch_observe_command",
    "dispatch_actuate_command",
    "dispatch_domain_agent",
]
