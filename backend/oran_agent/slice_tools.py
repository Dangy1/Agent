import time
from typing import Any, Dict, List, Optional

from typing_extensions import Annotated
from langchain.tools import tool
from langchain_mcp_adapters.tools import InjectedToolArg

from .core import _MCP, _call_mcp_with_intercept, _err, _extract_config_from_runtime_state, _format_exc
from .validators import _clamp_int, _validate_int_range, _validate_slice_start_inputs, _validate_slice_verify_inputs


@tool
def mcp_get_slice_state(mode: str = "summary", runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'get_slice_state'."""
    if mode not in ("summary", "raw"):
        return {"status": "error", "error": "mode must be 'summary' or 'raw'"}
    try:
        res = _MCP.call("get_slice_state", {"mode": mode})
        return {"status": "success", "mode": mode, "result": res}
    except Exception as e:
        return {
            "status": "error",
            "tool": "get_slice_state",
            "mode": mode,
            "error": _format_exc(e),
            "hint": (
                "If stdio: ensure server writes ONLY JSON-RPC to stdout. "
                "If HTTP: keep MCP server process persistent and use MCP_TRANSPORT=http."
            ),
        }


@tool
def mcp_create_slices(config: Optional[dict] = None, runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'create_slices'."""
    if config is None:
        recovered = _extract_config_from_runtime_state(runtime)
        if isinstance(recovered, dict):
            config = recovered
        else:
            return {
                "status": "error",
                "tool": "create_slices",
                "error": "Missing required argument: config",
                "hint": "Guided config expects mcp_create_slices(config={...}). I could not recover config from the latest user message.",
                "example": {
                    "config": {
                        "slice_sched_algo": "STATIC",
                        "slices": [
                            {
                                "id": 0,
                                "label": "s1",
                                "ue_sched_algo": "PF",
                                "slice_algo_params": {"pos_low": 0, "pos_high": 2},
                            }
                        ],
                    }
                },
            }
    out = _call_mcp_with_intercept("create_slices", {"config": config})
    if out.get("status") == "error":
        out.setdefault("config", config)
        err_text = str(out.get("error", ""))
        if "Unknown MCP tool 'create_slices'" in err_text:
            out["hint"] = (
                "Your current MCP server does not expose 'create_slices' (common with mcp_flexric_suites.py). "
                "Use mcp_slice_apply_profile_and_verify / mcp_slice_start, or switch to a slice-control MCP server."
            )
            out["alternatives"] = [
                "mcp_slice_apply_profile_and_verify(profile='static'|'nvs-rate'|'nvs-cap'|'edf'|'all', ...)",
                "mcp_slice_start(profile='monitor'|...)",
                "Switch MCP_SERVER_ARGS to a server that exposes create_slices",
            ]
    return out


@tool
def mcp_create_example_slices(profile: str = "static", runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'create_example_slices'."""
    try:
        return {"status": "success", "result": _MCP.call("create_example_slices", {"profile": profile})}
    except Exception as e:
        return {"status": "error", "tool": "create_example_slices", "error": _format_exc(e), "profile": profile}


@tool
def mcp_delete_slices(delete_dl_slice_id: List[int], runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'delete_slices'."""
    try:
        return {"status": "success", "result": _MCP.call("delete_slices", {"delete_dl_slice_id": delete_dl_slice_id})}
    except Exception as e:
        return {"status": "error", "tool": "delete_slices", "error": _format_exc(e), "delete_dl_slice_id": delete_dl_slice_id}


@tool
def mcp_associate_ues(ues: List[dict], runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'associate_ues'."""
    try:
        return {"status": "success", "result": _MCP.call("associate_ues", {"ues": ues})}
    except Exception as e:
        return {"status": "error", "tool": "associate_ues", "error": _format_exc(e), "ues": ues}


@tool
def mcp_reset_slices(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'reset_slices'."""
    try:
        return {"status": "success", "result": _MCP.call("reset_slices", {})}
    except Exception as e:
        return {"status": "error", "tool": "reset_slices", "error": _format_exc(e)}


@tool
def mcp_get_seen_ues(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'get_seen_ues'."""
    try:
        return {"status": "success", "result": _MCP.call("get_seen_ues", {})}
    except Exception as e:
        return {"status": "error", "tool": "get_seen_ues", "error": _format_exc(e)}


@tool
def mcp_slice_custom_config_capabilities(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Check whether the current MCP server supports arbitrary create_slices-style custom slice configs."""
    try:
        return {"status": "success", "result": _MCP.call("slice_custom_config_capabilities", {})}
    except Exception as e:
        return {
            "status": "error",
            "tool": "slice_custom_config_capabilities",
            "error": _format_exc(e),
            "hint": "If this MCP server does not expose slice_custom_config_capabilities, call mcp_list_tools and infer support from tool names.",
        }


@tool
def mcp_slice_start(
    profile: str = "monitor",
    duration_s: int = 180,
    verbose: bool = False,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Start Slice suite profile via MCP tool 'slice_start'."""
    guard = _validate_slice_start_inputs(profile, duration_s)
    if guard:
        return _err(
            "slice_start",
            guard,
            guidance={
                "profile": ["monitor", "static", "nvs-rate", "nvs-cap", "edf", "all"],
                "duration_s_range": [1, 3600],
                "tip": "Start with profile='monitor' and duration_s=30 to verify connectivity first.",
            },
        )
    try:
        args = {"profile": profile, "duration_s": int(duration_s), "verbose": bool(verbose)}
        return {"status": "success", "result": _MCP.call("slice_start", args)}
    except Exception as e:
        return {"status": "error", "tool": "slice_start", "error": _format_exc(e), "profile": profile}


@tool
def mcp_slice_monitor_check(
    duration_s: int = 30,
    verbose: bool = False,
    timeout_s: int = 10,
    tail_lines: int = 120,
    stop_after_check: bool = False,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Run deterministic MCP-side slice monitor check and return verification result."""
    try:
        duration_s_i = int(duration_s)
        timeout_s_i = int(timeout_s)
        tail_lines_i = int(tail_lines)
    except Exception as e:
        return {"status": "error", "tool": "slice_monitor_check", "error": f"Invalid numeric parameter: {_format_exc(e)}"}

    msg = _validate_int_range("duration_s", duration_s_i, 5, 600)
    if msg:
        return _err(
            "slice_monitor_check",
            msg,
            guidance={
                "duration_s_range": [5, 600],
                "timeout_s_range": [1, 120],
                "tail_lines_range": [20, 1000],
                "example": {"duration_s": 20, "verbose": True, "stop_after_check": True},
            },
        )
    warnings: List[str] = []
    if timeout_s_i < 1 or timeout_s_i > 120:
        new_timeout = _clamp_int(timeout_s_i, 1, 120)
        warnings.append(f"timeout_s adjusted from {timeout_s_i} to {new_timeout} (allowed range 1-120)")
        timeout_s_i = new_timeout
    if tail_lines_i < 20 or tail_lines_i > 1000:
        new_tail = _clamp_int(tail_lines_i, 20, 1000)
        warnings.append(f"tail_lines adjusted from {tail_lines_i} to {new_tail} (allowed range 20-1000)")
        tail_lines_i = new_tail
    try:
        args = {
            "duration_s": duration_s_i,
            "verbose": bool(verbose),
            "timeout_s": timeout_s_i,
            "tail_lines": tail_lines_i,
            "stop_after_check": bool(stop_after_check),
        }
        out = {"status": "success", "result": _MCP.call("slice_monitor_check", args)}
        if warnings:
            out["warnings"] = warnings
        return out
    except Exception as e:
        return {"status": "error", "tool": "slice_monitor_check", "error": _format_exc(e)}


@tool
def mcp_slice_apply_profile_and_verify(
    profile: str = "static",
    duration_s: int = 60,
    verbose: bool = True,
    assoc_dl_id: Optional[int] = None,
    startup_timeout_s: int = 15,
    verify_tail_lines: int = 160,
    stop_after_verify: bool = False,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Apply a slice profile and verify from MCP server side (status + logs)."""
    guard = _validate_slice_verify_inputs(
        profile=profile,
        duration_s=duration_s,
        startup_timeout_s=startup_timeout_s,
        verify_tail_lines=verify_tail_lines,
        assoc_dl_id=assoc_dl_id,
    )
    if guard:
        return _err(
            "slice_apply_profile_and_verify",
            guard,
            guidance={
                "profile": ["static", "nvs-rate", "nvs-cap", "edf", "all"],
                "duration_s_range": [1, 3600],
                "startup_timeout_s_range": [1, 120],
                "verify_tail_lines_range": [20, 1000],
                "assoc_dl_id_range": [0, 255],
                "tip": "Run mcp_slice_monitor_check first if runtime health is uncertain.",
            },
        )
    try:
        args: Dict[str, Any] = {
            "profile": profile,
            "duration_s": int(duration_s),
            "verbose": bool(verbose),
            "startup_timeout_s": int(startup_timeout_s),
            "verify_tail_lines": int(verify_tail_lines),
            "stop_after_verify": bool(stop_after_verify),
        }
        if assoc_dl_id is not None:
            args["assoc_dl_id"] = int(assoc_dl_id)
        return {"status": "success", "result": _MCP.call("slice_apply_profile_and_verify", args)}
    except Exception as e:
        return {"status": "error", "tool": "slice_apply_profile_and_verify", "error": _format_exc(e), "profile": profile}
