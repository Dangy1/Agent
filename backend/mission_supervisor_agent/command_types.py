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
    ("network", "health"),
    ("network", "slice_monitor"),
    ("network", "kpm_monitor"),
}


def classify_command_operation_type(command: Dict[str, Any]) -> str:
    domain = str(command.get("domain", ""))
    op = str(command.get("op", ""))
    if (domain, op) in OBSERVE_OPS:
        return "observe"
    if domain in {"uav", "utm", "network"} and op:
        return "actuate"
    return "unknown"


__all__ = ["OBSERVE_OPS", "classify_command_operation_type"]

