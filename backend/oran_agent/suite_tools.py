import time
from typing import Any, Dict, List, Optional

from typing_extensions import Annotated
from langchain.tools import tool
from langchain_mcp_adapters.tools import InjectedToolArg

from .core import _MCP, _err, _format_exc
from .validators import _clamp_int, _extract_kpm_indication_lines, _validate_int_range


@tool
def mcp_tc_start(
    profile: str,
    duration_s: int = 180,
    monitor_rlc: bool = False,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Start TC suite profile via MCP tool 'tc_start'."""
    try:
        args = {"profile": profile, "duration_s": int(duration_s), "monitor_rlc": bool(monitor_rlc)}
        return {"status": "success", "result": _MCP.call("tc_start", args)}
    except Exception as e:
        return {"status": "error", "tool": "tc_start", "error": _format_exc(e), "profile": profile}


@tool
def mcp_kpm_rc_start(
    profile: str = "kpm",
    period_ms: int = 1000,
    duration_s: int = 180,
    kpm_metrics: str = "rru",
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Start KPM/RC suite profile via MCP tool 'kpm_rc_start'."""
    try:
        args = {
            "profile": profile,
            "period_ms": int(period_ms),
            "duration_s": int(duration_s),
            "kpm_metrics": kpm_metrics,
        }
        return {"status": "success", "result": _MCP.call("kpm_rc_start", args)}
    except Exception as e:
        return {"status": "error", "tool": "kpm_rc_start", "error": _format_exc(e), "profile": profile}


@tool
def mcp_kpm_monitor_check(
    period_ms: int = 1000,
    duration_s: int = 30,
    kpm_metrics: str = "rru",
    startup_timeout_s: int = 15,
    poll_interval_ms: int = 1000,
    tail_lines: int = 120,
    stop_after_check: bool = False,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Run one KPM demo and return live indication lines by reading logs (native MCP tool if available, else fallback)."""
    try:
        period_ms_i = int(period_ms)
        duration_s_i = int(duration_s)
        startup_timeout_s_i = int(startup_timeout_s)
        poll_interval_ms_i = int(poll_interval_ms)
        tail_lines_i = int(tail_lines)
    except Exception as e:
        return _err("kpm_monitor_check", f"Invalid numeric parameter: {_format_exc(e)}")

    if (msg := _validate_int_range("period_ms", period_ms_i, 100, 60000)):
        return _err("kpm_monitor_check", msg)
    if (msg := _validate_int_range("duration_s", duration_s_i, 1, 3600)):
        return _err("kpm_monitor_check", msg)
    if (msg := _validate_int_range("startup_timeout_s", startup_timeout_s_i, 1, 120)):
        return _err("kpm_monitor_check", msg)
    if (msg := _validate_int_range("poll_interval_ms", poll_interval_ms_i, 200, 5000)):
        return _err("kpm_monitor_check", msg)
    if tail_lines_i < 20 or tail_lines_i > 1000:
        tail_lines_i = _clamp_int(tail_lines_i, 20, 1000)
    poll_interval_f = poll_interval_ms_i / 1000.0

    native_args = {
        "period_ms": period_ms_i,
        "duration_s": duration_s_i,
        "kpm_metrics": kpm_metrics,
        "startup_timeout_s": startup_timeout_s_i,
        "poll_interval_ms": poll_interval_ms_i,
        "tail_lines": tail_lines_i,
        "stop_after_check": bool(stop_after_check),
    }
    try:
        native = _MCP.call("kpm_monitor_check", native_args)
        return {"status": "success", "result": native, "source": "mcp:kpm_monitor_check"}
    except Exception as e:
        native_err = _format_exc(e)
        if "Unknown MCP tool 'kpm_monitor_check'" not in native_err:
            return {"status": "error", "tool": "kpm_monitor_check", "error": native_err}

    start_args = {
        "profile": "kpm",
        "period_ms": period_ms_i,
        "duration_s": duration_s_i,
        "kpm_metrics": kpm_metrics,
    }
    try:
        start_raw = _MCP.call("kpm_rc_start", start_args)
    except Exception as e:
        return {
            "status": "error",
            "tool": "kpm_monitor_check",
            "error": f"Fallback start failed: {_format_exc(e)}",
            "fallback_tool": "kpm_rc_start",
        }

    run = start_raw.get("run") if isinstance(start_raw, dict) and isinstance(start_raw.get("run"), dict) else (
        start_raw if isinstance(start_raw, dict) else {}
    )
    run_id = str(run.get("run_id", "") or "")
    suite = str(run.get("suite", "kpm_rc") or "kpm_rc")
    log_path = run.get("log_path")

    deadline = time.time() + startup_timeout_s_i
    latest_tail: List[str] = []
    indications: List[str] = []
    status_payload: Optional[Dict[str, Any]] = None
    tail_warnings: List[str] = []

    while time.time() < deadline:
        try:
            status_payload = _MCP.call("run_status", {"suite": suite})
        except Exception:
            status_payload = None

        try:
            tail_payload = _MCP.call("run_log_tail", {"suite": suite, "lines": tail_lines_i})
            tail_list = tail_payload.get("tail") if isinstance(tail_payload, dict) else None
            if isinstance(tail_list, list):
                latest_tail = [str(x) for x in tail_list]
                indications = _extract_kpm_indication_lines(latest_tail)
                if indications:
                    break
            if isinstance(tail_payload, dict) and isinstance(tail_payload.get("run"), dict) and not log_path:
                log_path = tail_payload["run"].get("log_path")
        except Exception as e:
            tail_warnings.append(_format_exc(e))

        time.sleep(poll_interval_f)

    out: Dict[str, Any] = {
        "status": "success",
        "source": "fallback:kpm_rc_start+run_log_tail",
        "run": run,
        "kpm": {
            "period_ms": period_ms_i,
            "duration_s": duration_s_i,
            "metrics": kpm_metrics,
            "poll_interval_ms": poll_interval_ms_i,
            "suite": suite,
            "run_id": run_id or None,
            "log_path": log_path,
            "indications_found": len(indications),
            "indications": indications,
        },
    }
    if status_payload is not None:
        out["run_status"] = status_payload
    if latest_tail:
        out["tail_excerpt"] = latest_tail[-min(20, len(latest_tail)) :]
    if not indications:
        out["warnings"] = [
            "No KPM indication lines detected before timeout. Use mcp_run_log_tail(suite='kpm_rc') to inspect more logs."
        ] + (tail_warnings[-2:] if tail_warnings else [])
    if stop_after_check:
        try:
            out["stop"] = _MCP.call("run_stop", {"suite": suite, "force": False})
        except Exception as e:
            out["stop_error"] = _format_exc(e)
    return out


@tool
def mcp_runs_list(active_only: bool = False, runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """List MCP-managed suite runs."""
    try:
        return {"status": "success", "result": _MCP.call("runs_list", {"active_only": bool(active_only)})}
    except Exception as e:
        return {"status": "error", "tool": "runs_list", "error": _format_exc(e)}


@tool
def mcp_run_status(
    run_id: Optional[str] = None,
    suite: Optional[str] = None,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Get status for a suite run by run_id or active suite name."""
    try:
        args: Dict[str, Any] = {}
        if run_id:
            args["run_id"] = run_id
        if suite:
            args["suite"] = suite
        return {"status": "success", "result": _MCP.call("run_status", args)}
    except Exception as e:
        return {"status": "error", "tool": "run_status", "error": _format_exc(e)}


@tool
def mcp_run_log_tail(
    lines: int = 80,
    run_id: Optional[str] = None,
    suite: Optional[str] = None,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Tail log of a managed suite run."""
    try:
        args: Dict[str, Any] = {"lines": int(lines)}
        if run_id:
            args["run_id"] = run_id
        if suite:
            args["suite"] = suite
        return {"status": "success", "result": _MCP.call("run_log_tail", args)}
    except Exception as e:
        return {"status": "error", "tool": "run_log_tail", "error": _format_exc(e)}


@tool
def mcp_run_stop(
    run_id: Optional[str] = None,
    suite: Optional[str] = None,
    force: bool = False,
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Stop a managed suite run by run_id or suite."""
    try:
        args: Dict[str, Any] = {"force": bool(force)}
        if run_id:
            args["run_id"] = run_id
        if suite:
            args["suite"] = suite
        return {"status": "success", "result": _MCP.call("run_stop", args)}
    except Exception as e:
        return {"status": "error", "tool": "run_stop", "error": _format_exc(e)}


@tool
def mcp_stop_all(force: bool = False, runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Stop all managed suite runs."""
    try:
        return {"status": "success", "result": _MCP.call("stop_all", {"force": bool(force)})}
    except Exception as e:
        return {"status": "error", "tool": "stop_all", "error": _format_exc(e)}
