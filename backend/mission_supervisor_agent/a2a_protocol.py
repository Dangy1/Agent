from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from .command_types import classify_command_operation_type

# A2A JSON-RPC request envelope (https://a2a-protocol.org).
A2A_PROTOCOL_ID = "A2A"
A2A_PROTOCOL_VERSION = "0.2.6"
A2A_JSONRPC_VERSION = "2.0"
A2A_SEND_MESSAGE_METHOD = "message/send"
MCP_PROTOCOL_ID = "modelcontextprotocol.io/jsonrpc"
MCP_PROTOCOL_VERSION = "2024-11-05"

_MCP_TOOL_MAP: Dict[Tuple[str, str], str] = {
    # UAV tools
    ("uav", "plan_route"): "uav_plan_route",
    ("uav", "submit_route_geofence"): "uav_submit_route_to_utm_geofence_check",
    ("uav", "submit_route_to_utm_geofence_check"): "uav_submit_route_to_utm_geofence_check",
    ("uav", "request_utm_approval"): "uav_request_utm_approval",
    ("uav", "utm_approval"): "uav_request_utm_approval",
    ("uav", "launch"): "uav_launch",
    ("uav", "step"): "uav_sim_step",
    ("uav", "sim_step"): "uav_sim_step",
    ("uav", "status"): "uav_status",
    ("uav", "hold"): "uav_hold",
    ("uav", "rth"): "uav_return_to_home",
    ("uav", "return_to_home"): "uav_return_to_home",
    ("uav", "land"): "uav_land",
    # UTM tools
    ("utm", "verify_flight_plan"): "utm_verify_flight_plan",
    ("utm", "check_geofence"): "utm_check_geofence",
    ("utm", "weather_check"): "utm_weather_check",
    ("utm", "no_fly_zone_check"): "utm_no_fly_zone_check",
    ("utm", "regulation_check"): "utm_regulation_check",
    ("utm", "time_window_check"): "utm_time_window_check",
    ("utm", "operator_license_check"): "utm_operator_license_check",
    ("utm", "reserve_corridor"): "utm_reserve_corridor",
    ("utm", "set_weather"): "utm_set_weather",
    ("utm", "upsert_operational_intent"): "utm_dss_upsert_operational_intent",
    ("utm", "dss_upsert_operational_intent"): "utm_dss_upsert_operational_intent",
    ("utm", "delete_operational_intent"): "utm_dss_delete_operational_intent",
    ("utm", "dss_delete_operational_intent"): "utm_dss_delete_operational_intent",
    ("utm", "query_operational_intents"): "utm_dss_query_operational_intents",
    ("utm", "dss_query_operational_intents"): "utm_dss_query_operational_intents",
    ("utm", "upsert_subscription"): "utm_dss_upsert_subscription",
    ("utm", "dss_upsert_subscription"): "utm_dss_upsert_subscription",
    ("utm", "delete_subscription"): "utm_dss_delete_subscription",
    ("utm", "dss_delete_subscription"): "utm_dss_delete_subscription",
    ("utm", "query_subscriptions"): "utm_dss_query_subscriptions",
    ("utm", "dss_query_subscriptions"): "utm_dss_query_subscriptions",
    ("utm", "upsert_participant"): "utm_dss_upsert_participant",
    ("utm", "dss_upsert_participant"): "utm_dss_upsert_participant",
    ("utm", "delete_participant"): "utm_dss_delete_participant",
    ("utm", "dss_delete_participant"): "utm_dss_delete_participant",
    ("utm", "query_participants"): "utm_dss_query_participants",
    ("utm", "dss_query_participants"): "utm_dss_query_participants",
    ("utm", "ack_notification"): "utm_dss_ack_notification",
    ("utm", "dss_ack_notification"): "utm_dss_ack_notification",
    ("utm", "query_notifications"): "utm_dss_query_notifications",
    ("utm", "dss_query_notifications"): "utm_dss_query_notifications",
    ("utm", "run_local_conformance"): "utm_dss_run_local_conformance",
    ("utm", "dss_run_local_conformance"): "utm_dss_run_local_conformance",
    ("utm", "conformance_last"): "utm_dss_get_last_conformance",
    ("utm", "dss_conformance_last"): "utm_dss_get_last_conformance",
    # Network/O-RAN MCP tools
    ("network", "health"): "mcp_health",
    ("network", "slice_monitor"): "mcp_slice_monitor_check",
    ("network", "slice_apply_profile"): "mcp_slice_apply_profile_and_verify",
    ("network", "tc_start"): "mcp_tc_start",
    ("network", "kpm_start"): "mcp_kpm_rc_start",
    ("network", "kpm_monitor"): "mcp_kpm_monitor_check",
    ("network", "runs_list"): "mcp_runs_list",
    ("network", "run_status"): "mcp_run_status",
    ("network", "run_log_tail"): "mcp_run_log_tail",
    # DSS tools
    ("dss", "state"): "dss_state",
    ("dss", "upsert_operational_intent"): "dss_upsert_operational_intent",
    ("dss", "dss_upsert_operational_intent"): "dss_upsert_operational_intent",
    ("dss", "query_operational_intents"): "dss_query_operational_intents",
    ("dss", "dss_query_operational_intents"): "dss_query_operational_intents",
    ("dss", "delete_operational_intent"): "dss_delete_operational_intent",
    ("dss", "dss_delete_operational_intent"): "dss_delete_operational_intent",
    ("dss", "upsert_subscription"): "dss_upsert_subscription",
    ("dss", "dss_upsert_subscription"): "dss_upsert_subscription",
    ("dss", "query_subscriptions"): "dss_query_subscriptions",
    ("dss", "dss_query_subscriptions"): "dss_query_subscriptions",
    ("dss", "delete_subscription"): "dss_delete_subscription",
    ("dss", "dss_delete_subscription"): "dss_delete_subscription",
    ("dss", "upsert_participant"): "dss_upsert_participant",
    ("dss", "dss_upsert_participant"): "dss_upsert_participant",
    ("dss", "query_participants"): "dss_query_participants",
    ("dss", "dss_query_participants"): "dss_query_participants",
    ("dss", "delete_participant"): "dss_delete_participant",
    ("dss", "dss_delete_participant"): "dss_delete_participant",
    ("dss", "query_notifications"): "dss_query_notifications",
    ("dss", "dss_query_notifications"): "dss_query_notifications",
    ("dss", "ack_notification"): "dss_ack_notification",
    ("dss", "dss_ack_notification"): "dss_ack_notification",
    ("dss", "run_local_conformance"): "dss_run_local_conformance",
    ("dss", "dss_run_local_conformance"): "dss_run_local_conformance",
    ("dss", "conformance_last"): "dss_conformance_last",
    ("dss", "dss_conformance_last"): "dss_conformance_last",
    # USS tools
    ("uss", "state"): "uss_state",
    ("uss", "publish_intent"): "uss_publish_intent",
    ("uss", "upsert_operational_intent"): "uss_publish_intent",
    ("uss", "uss_publish_intent"): "uss_publish_intent",
    ("uss", "query_peer_intents"): "uss_query_intents",
    ("uss", "query_operational_intents"): "uss_query_intents",
    ("uss", "uss_query_intents"): "uss_query_intents",
    ("uss", "subscribe_airspace"): "uss_subscribe_airspace",
    ("uss", "upsert_subscription"): "uss_subscribe_airspace",
    ("uss", "uss_subscribe_airspace"): "uss_subscribe_airspace",
    ("uss", "pull_notifications"): "uss_pull_notifications",
    ("uss", "uss_pull_notifications"): "uss_pull_notifications",
    ("uss", "ack_notification"): "uss_ack_notification",
    ("uss", "uss_ack_notification"): "uss_ack_notification",
    ("uss", "register_participant"): "uss_register_participant",
    ("uss", "uss_register_participant"): "uss_register_participant",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_str(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _to_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def command_to_mcp_tool(command: Dict[str, Any]) -> str:
    domain = _clean_str(command.get("domain"))
    op = _clean_str(command.get("op"))
    return _MCP_TOOL_MAP.get((domain, op), f"{domain}.{op}" if domain and op else op or "unknown")


def command_to_mcp_invocation(command: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    params = _to_dict(command.get("params"))
    mission_id = _clean_str(state.get("mission_id"), default="mission-local")
    correlation_id = _clean_str(state.get("task_id"), default=mission_id)
    tool = command_to_mcp_tool(command)
    return {
        "protocol": MCP_PROTOCOL_ID,
        "version": MCP_PROTOCOL_VERSION,
        "correlation_id": correlation_id,
        "mission_id": mission_id,
        "tool": tool,
        "arguments": params,
    }


def _command_summary_text(cmd: Dict[str, Any], intent_type: str, sender: str, receiver: str) -> str:
    domain = _clean_str(cmd.get("domain"), default="unknown")
    op = _clean_str(cmd.get("op"), default="unknown")
    step_id = _clean_str(cmd.get("step_id"))
    if step_id:
        return f"[{sender} -> {receiver}] {intent_type} {domain}.{op} (step={step_id})"
    return f"[{sender} -> {receiver}] {intent_type} {domain}.{op}"


@dataclass(frozen=True)
class A2AEnvelope:
    protocol: str
    version: str
    jsonrpc: str
    id: str
    method: str
    params: Dict[str, Any]
    # Legacy aliases are preserved to avoid breaking existing mission traces.
    message_id: str
    parent_message_id: str | None
    created_at: str
    correlation_id: str
    mission_id: str
    sender: str
    receiver: str
    intent_type: str
    command: Dict[str, Any]
    constraints: Dict[str, Any]
    metadata: Dict[str, Any]

    @classmethod
    def from_command(
        cls,
        command: Dict[str, Any],
        state: Dict[str, Any],
        *,
        sender: str = "mission_supervisor",
        receiver: str | None = None,
    ) -> "A2AEnvelope":
        cmd = {
            "domain": _clean_str(command.get("domain")),
            "op": _clean_str(command.get("op")),
            "params": _to_dict(command.get("params")),
            "step_id": _clean_str(command.get("step_id")),
        }
        mission_id = _clean_str(state.get("mission_id"), default="mission-local")
        correlation_id = _clean_str(state.get("task_id"), default=mission_id)
        created_at = _utc_now()
        digest = _stable_hash(
            {
                "correlation_id": correlation_id,
                "mission_id": mission_id,
                "command": cmd,
                "created_at": created_at,
            }
        )[:16]
        operation_type = classify_command_operation_type(cmd)
        intent_type = "observe" if operation_type == "observe" else ("actuate" if operation_type == "actuate" else "unknown")
        params = cmd["params"]
        deterministic_hint = bool(params.get("_idempotent")) or operation_type == "observe"
        message_id = f"a2a-{digest}"
        resolved_receiver = receiver or _clean_str(cmd.get("domain"), default="unknown")
        message: Dict[str, Any] = {
            # `kind` is still commonly used by current A2A SDK examples.
            "kind": "message",
            "messageId": message_id,
            "contextId": mission_id,
            "taskId": correlation_id,
            "role": "agent",
            "parts": [
                {"kind": "text", "text": _command_summary_text(cmd, intent_type, sender, resolved_receiver)},
                {"kind": "data", "data": dict(cmd), "metadata": {"contentType": "application/json"}},
            ],
            "metadata": {
                "sender": sender,
                "receiver": resolved_receiver,
                "intentType": intent_type,
                "missionPhase": _clean_str(state.get("mission_phase"), default="unknown"),
                "deterministicHint": deterministic_hint,
                "taskIdempotencyKey": _clean_str(state.get("task_idempotency_key")),
            },
        }
        request_params: Dict[str, Any] = {
            "message": message,
            "configuration": {
                "acceptedOutputModes": ["application/json"],
                "historyLength": 0,
                "blocking": True,
            },
        }
        return cls(
            protocol=A2A_PROTOCOL_ID,
            version=A2A_PROTOCOL_VERSION,
            jsonrpc=A2A_JSONRPC_VERSION,
            id=message_id,
            method=A2A_SEND_MESSAGE_METHOD,
            params=request_params,
            message_id=message_id,
            parent_message_id=_clean_str(state.get("parent_message_id")) or None,
            created_at=created_at,
            correlation_id=correlation_id,
            mission_id=mission_id,
            sender=sender,
            receiver=resolved_receiver,
            intent_type=intent_type,
            command=cmd,
            constraints={"deterministic_hint": deterministic_hint},
            metadata={
                "mission_phase": _clean_str(state.get("mission_phase"), default="unknown"),
                "task_idempotency_key": _clean_str(state.get("task_idempotency_key")),
            },
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "protocol": self.protocol,
            "version": self.version,
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": dict(self.params),
            "message_id": self.message_id,
            "parent_message_id": self.parent_message_id,
            "created_at": self.created_at,
            "correlation_id": self.correlation_id,
            "mission_id": self.mission_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "intent_type": self.intent_type,
            "command": dict(self.command),
            "constraints": dict(self.constraints),
            "metadata": dict(self.metadata),
        }


__all__ = [
    "A2AEnvelope",
    "A2A_PROTOCOL_ID",
    "A2A_PROTOCOL_VERSION",
    "A2A_JSONRPC_VERSION",
    "A2A_SEND_MESSAGE_METHOD",
    "MCP_PROTOCOL_ID",
    "MCP_PROTOCOL_VERSION",
    "command_to_mcp_invocation",
    "command_to_mcp_tool",
]
