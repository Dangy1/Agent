from __future__ import annotations

from typing import Any, Dict, Set, Tuple


OBSERVE_OPS: Set[Tuple[str, str]] = {
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


def classify_command_operation_type(command: Dict[str, Any]) -> str:
    domain = str(command.get("domain", ""))
    op = str(command.get("op", ""))
    if (domain, op) in OBSERVE_OPS:
        return "observe"
    if domain in {"uav", "utm", "network", "dss", "uss"} and op:
        return "actuate"
    return "unknown"


__all__ = ["OBSERVE_OPS", "classify_command_operation_type"]
