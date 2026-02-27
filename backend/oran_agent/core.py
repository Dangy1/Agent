#!/usr/bin/env python3
"""
agent.py - minimal AgentRIC (Ollama + MCP transport auto/http/stdio)

Unified FlexRIC suites variant:
- Keeps MCP client object alive (prevents session teardown/disconnect churn).
- Supports HTTP or stdio MCP transport from env.
- Defaults to stdio MCP suites server (mcp_flexric_suites.py).
- Calls async-only MCP tools safely via ainvoke() first.
"""

import sys
import shlex
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from typing_extensions import Annotated

from langchain.tools import tool
from langchain.agents import AgentState

from langchain_mcp_adapters.tools import InjectedToolArg

from .config.settings import (
    AUDIT_LOG_PATH,
    MCP_CALL_TIMEOUT_S,
    MCP_HTTP_AUTH_TOKEN,
    MCP_HTTP_URL,
    MCP_SERVER_ARGS,
    MCP_SERVER_CMD,
    MCP_SERVER_NAME,
    MCP_TRANSPORT,
)
from .config.runtime_mcp import runtime_snapshot_for_ui
from .mcp_bridge import MCPBridge
from .validators import (
    _clamp_int,
    _extract_kpm_indication_lines,
    _validate_int_range,
    _validate_slice_config,
    _validate_slice_start_inputs,
    _validate_slice_verify_inputs,
)


# ============================================================
# Runtime Context + State
# ============================================================
@dataclass(frozen=True)
class Context:
    user_id: str = ""
    session_id: str = ""


class CustomState(AgentState):
    did_list_tools: bool
    mcp_tools: list[dict]


# ============================================================
# Helpers
# ============================================================
def _server_cmd_list() -> List[str]:
    cmd = [MCP_SERVER_CMD]
    if MCP_SERVER_ARGS:
        cmd += shlex.split(MCP_SERVER_ARGS)
    return cmd


def _format_exc(e: BaseException) -> str:
    if isinstance(e, ExceptionGroup):
        lines = [f"{type(e).__name__}: {e}"]
        for i, sub in enumerate(e.exceptions, 1):
            lines.append(f"  [{i}] {type(sub).__name__}: {sub}")
        return "\n".join(lines)
    return f"{type(e).__name__}: {e}"


def _is_sync_invocation_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return "does not support sync invocation" in msg


def _audit_log_path() -> str:
    return AUDIT_LOG_PATH


def _safe_json(value: Any, max_len: int = 1200) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=True)
    except Exception:
        text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _summarize_args(args: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key == "config" and isinstance(value, dict):
            slices = value.get("slices") or []
            summary[key] = {
                "slice_sched_algo": value.get("slice_sched_algo"),
                "num_slices": value.get("num_slices", len(slices) if isinstance(slices, list) else None),
                "slice_ids": [s.get("id") for s in slices if isinstance(s, dict)][:10] if isinstance(slices, list) else [],
            }
            continue
        summary[key] = value
    return summary


def _append_audit(event: str, payload: Dict[str, Any]) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "payload": payload,
    }
    try:
        path = _audit_log_path()
        try:
            from pathlib import Path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(_safe_json(rec, max_len=6000) + "\n")
    except Exception:
        # Logging must never break agent execution.
        pass




def _extract_json_after_marker(text: str, marker: str = "config=") -> Optional[dict]:
    """Extract the first JSON object appearing after marker, skipping non-JSON marker matches."""
    pos = 0
    while True:
        idx = text.find(marker, pos)
        if idx < 0:
            return None
        s = text[idx + len(marker):].lstrip()
        if not s.startswith("{"):
            pos = idx + len(marker)
            continue

        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(s):
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
                        obj = json.loads(s[: i + 1])
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        break
        pos = idx + len(marker)


def _extract_config_from_runtime_state(runtime: Any) -> Optional[dict]:
    """Best-effort fallback for mcp_create_slices(config=...) when the model omits tool args."""
    state = getattr(runtime, "state", None)
    if not isinstance(state, dict):
        return None
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None

    for msg in reversed(messages):
        # HumanMessage from LangChain usually has `.type == "human"` and `.content`
        mtype = str(getattr(msg, "type", "") or (msg.get("type") if isinstance(msg, dict) else "")).lower()
        if mtype and mtype != "human":
            continue

        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if not isinstance(content, str):
            continue

        cfg = _extract_json_after_marker(content, "config=")
        if isinstance(cfg, dict):
            return cfg
        # tolerate "config: {...}" too
        cfg = _extract_json_after_marker(content.replace("config:", "config="), "config=")
        if isinstance(cfg, dict):
            return cfg
    return None


def _call_mcp_with_intercept(tool_name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    call_args = args or {}
    _append_audit("mcp_call_start", {"tool": tool_name, "args": _summarize_args(call_args)})

    if tool_name == "create_slices":
        validation = _validate_slice_config(call_args.get("config"))
        if not validation["ok"]:
            out = {
                "status": "error",
                "tool": tool_name,
                "error": "Slice config validation failed before MCP call",
                "guardrail": validation,
                "hint": (
                    "Check numeric ranges and required fields. "
                    "For NVS CAPACITY use pct_rsvd in (0,1], and for STATIC ensure pos_low <= pos_high."
                ),
            }
            _append_audit("mcp_call_blocked", {"tool": tool_name, "guardrail": validation})
            return out
        if validation["warnings"]:
            _append_audit("mcp_call_guardrail_warning", {"tool": tool_name, "warnings": validation["warnings"]})

    try:
        raw = _MCP.call(tool_name, call_args)
        out = {"status": "success", "result": raw}
        if tool_name == "create_slices":
            validation = _validate_slice_config(call_args.get("config"))
            if validation.get("warnings"):
                out["guardrail"] = {"warnings": validation["warnings"]}
            out["summary"] = {
                "message": "Slice create request sent to MCP server",
                "slice_count": len((call_args.get("config") or {}).get("slices", []) or []),
                "slice_sched_algo": (call_args.get("config") or {}).get("slice_sched_algo"),
            }
        _append_audit("mcp_call_success", {"tool": tool_name, "result": _summarize_args({"result": raw})})
        return out
    except Exception as e:
        err = {"status": "error", "tool": tool_name, "error": _format_exc(e)}
        _append_audit("mcp_call_error", {"tool": tool_name, "error": _format_exc(e)})
        return err


def _err(tool: str, message: str, **extra: Any) -> dict:
    out = {"status": "error", "tool": tool, "error": message}
    out.update(extra)
    return out


_MCP = MCPBridge()


# ============================================================
# Agent-facing tools
# ============================================================
@tool
def mcp_list_tools(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """List tools exposed by the configured MCP server."""
    try:
        tools = _MCP.list_tools_meta()
    except Exception as e:
        snap = runtime_snapshot_for_ui()
        return {
            "status": "error",
            "tool": "mcp_list_tools",
            "error": _format_exc(e),
            "transport": snap["transport"],
            "spawn": snap["spawn"],
            "http": snap["http"],
            "hint": (
                "If stdio: let client spawn server and keep stdout JSON-only. "
                "If HTTP: set MCP_TRANSPORT=http and MCP_HTTP_URL to your /mcp endpoint."
            ),
        }

    st = getattr(runtime, "state", None)
    if isinstance(st, dict):
        st["did_list_tools"] = True
        st["mcp_tools"] = tools

    return {"status": "success", "count": len(tools), "tools": tools}


@tool
def mcp_start(
    node_index: Optional[int] = None,
    interval_ms: Optional[int] = None,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Call MCP server tool 'start'."""
    try:
        call_args: Dict[str, Any] = {}
        if node_index is not None:
            call_args["node_index"] = int(node_index)
        if interval_ms is not None:
            call_args["interval_ms"] = int(interval_ms)
        res = _MCP.call("start", call_args)
        return {"status": "success", "result": res}
    except Exception as e:
        return {
            "status": "error",
            "tool": "start",
            "error": _format_exc(e),
            "hint": (
                "If stdio: ensure server writes ONLY JSON-RPC to stdout. "
                "If HTTP: keep MCP server process persistent and use MCP_TRANSPORT=http."
            ),
        }


@tool
def mcp_stop(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'stop'."""
    try:
        return {"status": "success", "result": _MCP.call("stop", {})}
    except Exception as e:
        return {"status": "error", "tool": "stop", "error": _format_exc(e)}


@tool
def mcp_list_e2_nodes(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'list_e2_nodes'."""
    try:
        return {"status": "success", "result": _MCP.call("list_e2_nodes", {})}
    except Exception as e:
        return {"status": "error", "tool": "list_e2_nodes", "error": _format_exc(e)}


@tool
def mcp_ric_init(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'ric_init'."""
    try:
        return {"status": "success", "result": _MCP.call("ric_init", {})}
    except Exception as e:
        return {"status": "error", "tool": "ric_init", "error": _format_exc(e)}


@tool
def mcp_ric_conn_e2_nodes(refresh: bool = True, runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'ric_conn_e2_nodes'."""
    try:
        return {"status": "success", "result": _MCP.call("ric_conn_e2_nodes", {"refresh": refresh})}
    except Exception as e:
        return {"status": "error", "tool": "ric_conn_e2_nodes", "error": _format_exc(e), "refresh": refresh}


@tool
def mcp_set_node_index(index: int, runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'set_node_index'."""
    try:
        return {"status": "success", "result": _MCP.call("set_node_index", {"index": index})}
    except Exception as e:
        return {"status": "error", "tool": "set_node_index", "error": _format_exc(e), "index": index}


from .slice_tools import (
    mcp_associate_ues,
    mcp_create_example_slices,
    mcp_create_slices,
    mcp_delete_slices,
    mcp_get_seen_ues,
    mcp_get_slice_state,
    mcp_reset_slices,
)


@tool
def mcp_health(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'health'."""
    try:
        return {"status": "success", "result": _MCP.call("health", {})}
    except Exception as e:
        return {"status": "error", "tool": "health", "error": _format_exc(e)}


from .slice_tools import (
    mcp_slice_apply_profile_and_verify,
    mcp_slice_custom_config_capabilities,
    mcp_slice_monitor_check,
    mcp_slice_start,
)
from .suite_tools import (
    mcp_kpm_monitor_check,
    mcp_kpm_rc_start,
    mcp_run_log_tail,
    mcp_run_status,
    mcp_run_stop,
    mcp_runs_list,
    mcp_stop_all,
    mcp_tc_start,
)


@tool
def mcp_call_tool(
    tool_name: str,
    args: Optional[dict] = None,
    args_json: Any = "",
    runtime: Annotated[Any, InjectedToolArg] = None,
    **extra: Any,
) -> dict:
    """Call any MCP tool by name. args can be dict, or args_json as JSON string/dict."""
    call_args: Dict[str, Any] = {}
    if isinstance(args, dict):
        call_args = args
    elif isinstance(args_json, dict):
        call_args = args_json
    elif isinstance(args_json, str) and args_json.strip():
        try:
            decoded = json.loads(args_json)
            if not isinstance(decoded, dict):
                return {"status": "error", "tool": tool_name, "error": "args_json must decode to a JSON object"}
            call_args = decoded
        except Exception as e:
            return {"status": "error", "tool": tool_name, "error": f"Invalid args_json: {_format_exc(e)}"}

    # Some model/tooling stacks emit internal vararg placeholders.
    extra_v_kwargs = extra.get("v__kwargs")
    extra_v_args = extra.get("v__args")
    if isinstance(extra_v_kwargs, dict):
        if not call_args:
            call_args = {k: v for k, v in extra_v_kwargs.items() if not str(k).startswith("v__")}
        elif not args and not (isinstance(args_json, str) and args_json.strip()):
            call_args.update({k: v for k, v in extra_v_kwargs.items() if not str(k).startswith("v__")})
    if isinstance(extra_v_args, list) and not call_args and len(extra_v_args) == 1 and isinstance(extra_v_args[0], dict):
        call_args = extra_v_args[0]

    if tool_name == "create_slices":
        out = _call_mcp_with_intercept(tool_name, call_args)
        out["tool"] = tool_name
        out["args"] = call_args
        return out

    try:
        raw = _MCP.call(tool_name, call_args)
        return {"status": "success", "tool": tool_name, "args": call_args, "result": raw}
    except Exception as e:
        return {"status": "error", "tool": tool_name, "args": call_args, "error": _format_exc(e)}


@tool
def session_info(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """Return minimal session/state info."""
    state = getattr(runtime, "state", {}) or {}
    snap = runtime_snapshot_for_ui()
    return {
        "status": "success",
        "state": {
            "did_list_tools": state.get("did_list_tools", False),
            "mcp_tools_count": len(state.get("mcp_tools", []) or []),
        },
        "transport": snap["transport"],
        "http": snap["http"],
        "spawn": snap["spawn"],
    }


TOOLS = [
    mcp_list_tools,
    mcp_tc_start,
    mcp_slice_start,
    mcp_kpm_rc_start,
    mcp_kpm_monitor_check,
    mcp_slice_monitor_check,
    mcp_slice_apply_profile_and_verify,
    mcp_runs_list,
    mcp_run_status,
    mcp_run_log_tail,
    mcp_run_stop,
    mcp_stop_all,
    mcp_ric_init,
    mcp_ric_conn_e2_nodes,
    mcp_set_node_index,
    mcp_start,
    mcp_stop,
    mcp_list_e2_nodes,
    mcp_create_slices,
    mcp_create_example_slices,
    mcp_delete_slices,
    mcp_associate_ues,
    mcp_reset_slices,
    mcp_get_slice_state,
    mcp_get_seen_ues,
    mcp_health,
    mcp_slice_custom_config_capabilities,
    mcp_call_tool,
    session_info,
]


# ============================================================
# Minimal agent
# ============================================================
BASE_SYSTEM_PROMPT = """You are AgentRIC.
Use tools:
- mcp_list_tools to inspect server tools
- mcp_tc_start(profile, duration_s, monitor_rlc) for TC suite demos
- mcp_slice_start(profile, duration_s, verbose) for Slice suite demos
- Prefer mcp_slice_monitor_check(...) before slice control when runtime health is uncertain
- Prefer mcp_slice_apply_profile_and_verify(...) for deterministic slice automation and evidence-based results
- mcp_kpm_rc_start(profile, period_ms, duration_s, kpm_metrics) for KPM/RC suite demos
- Prefer mcp_kpm_monitor_check(...) for a single KPM demo when the user wants live indications/log evidence in the response
- mcp_runs_list, mcp_run_status, mcp_run_log_tail, mcp_run_stop, mcp_stop_all to manage suite runs
- mcp_health for MCP server health
- mcp_slice_custom_config_capabilities to check whether arbitrary create_slices(config) is supported by the current MCP server
- Legacy slice tools remain available if the MCP server exposes them
- mcp_call_tool(tool_name, args/args_json) for direct MCP passthrough when needed

When the user wants to create slices, guide them step-by-step and validate numeric ranges before calling mcp_create_slices:
- STATIC: pos_low and pos_high are integers >= 0 and pos_low <= pos_high
- NVS RATE: mbps_rsvd and mbps_ref are integers > 0 and mbps_rsvd <= mbps_ref
- NVS CAPACITY: pct_rsvd is a number in (0, 1], and total CAPACITY percentages should not exceed 1.0
- EDF: deadline > 0, guaranteed_prbs >= 0, max_replenish >= 0
If values are invalid or missing, ask for corrected values instead of calling the tool.
If the current MCP server is flexric-suites (profile-based), check/mention that arbitrary create_slices(config) may be unsupported and offer profile-based alternatives.
For a single KPM demo, prefer mcp_kpm_monitor_check so you can show KPM status and live indication lines from logs directly in your response.
If a tool fails, report the exact tool error and hint; do not claim chat-interface limitations.
Do not repeat the same failing tool call more than once with the same arguments. If a tool fails, stop and explain what happened and what the user can do next.
"""
