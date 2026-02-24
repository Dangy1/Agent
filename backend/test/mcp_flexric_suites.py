#!/usr/bin/env python3
"""
mcp_flexric_suites.py (STDIO MCP server)

Unified MCP stdio server for FlexRIC Python suite demos:
- TC suite        (xapp_tc_suite.py)
- Slice suite     (xapp_slice_suite.py)
- KPM/RC suite    (xapp_kpm_rc_suite.py)

Design:
- Run suites as subprocesses (not in-process SWIG calls) to avoid SDK lifecycle conflicts.
- Keep MCP stdout clean (JSON-RPC only). Suite logs go to per-run log files.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP


logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp-flexric-suites")

mcp = FastMCP("flexric-suites")

_LOCK = threading.RLock()
_RUN_SEQ = 0


def _now_iso() -> str:
    return datetime.now().isoformat()


def _this_dir() -> Path:
    return Path(__file__).resolve().parent


def _suite_script(suite: str) -> Path:
    mapping = {
        "tc": "xapp_tc_suite.py",
        "slice": "xapp_slice_suite.py",
        "kpm_rc": "xapp_kpm_rc_suite.py",
    }
    return _this_dir() / mapping[suite]


def _log_root() -> Path:
    p = Path(os.getenv("FLEXRIC_MCP_LOG_DIR", "/tmp/flexric_mcp_runs"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _python_cmd() -> List[str]:
    # Use current interpreter by default (recommended: launch MCP server from working conda env)
    py = os.getenv("FLEXRIC_MCP_PYTHON", sys.executable)
    return [py, "-u"]


@dataclass
class RunState:
    run_id: str
    suite: str
    profile: str
    cmd: List[str]
    cwd: str
    log_path: str
    started_at: str
    pid: Optional[int] = None
    status: str = "starting"  # starting | running | exited | failed | stopped
    returncode: Optional[int] = None
    ended_at: Optional[str] = None
    error: Optional[str] = None
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)

    def to_public(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "profile": self.profile,
            "pid": self.pid,
            "status": self.status,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "cwd": self.cwd,
            "log_path": self.log_path,
            "cmd": self.cmd,
            "error": self.error,
        }


_RUNS: Dict[str, RunState] = {}
_ACTIVE_BY_SUITE: Dict[str, str] = {}


def _poll_locked() -> None:
    for run in _RUNS.values():
        if run.proc is None or run.status not in ("starting", "running"):
            continue
        rc = run.proc.poll()
        if rc is None:
            if run.status == "starting":
                run.status = "running"
            continue
        run.returncode = rc
        run.ended_at = _now_iso()
        if run.status == "stopped":
            pass
        elif rc == 0:
            run.status = "exited"
        else:
            run.status = "failed"
        if _ACTIVE_BY_SUITE.get(run.suite) == run.run_id:
            _ACTIVE_BY_SUITE.pop(run.suite, None)


def _tail_file(path: str, lines: int = 50) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            data = f.readlines()
        return "".join(data[-max(1, lines):])
    except FileNotFoundError:
        return ""


def _new_run_id(suite: str, profile: str) -> str:
    global _RUN_SEQ
    with _LOCK:
        _RUN_SEQ += 1
        return f"{suite}-{profile}-{_RUN_SEQ}"


def _spawn_suite(suite: str, profile: str, extra_args: List[str], stop_existing: bool) -> Dict[str, Any]:
    script = _suite_script(suite)
    if not script.exists():
        return {"status": "error", "error": f"Suite script not found: {script}"}

    with _LOCK:
        _poll_locked()
        active_id = _ACTIVE_BY_SUITE.get(suite)
        if active_id:
            active = _RUNS.get(active_id)
            if active and active.status in ("starting", "running"):
                if not stop_existing:
                    return {
                        "status": "error",
                        "error": f"{suite} suite already running (run_id={active_id})",
                        "active": active.to_public(),
                    }
                _terminate_locked(active, force=False)

        run_id = _new_run_id(suite, profile)
        run_log_dir = _log_root() / suite
        run_log_dir.mkdir(parents=True, exist_ok=True)
        log_path = run_log_dir / f"{run_id}.log"
        cmd = _python_cmd() + [str(script), "--profile", profile] + extra_args
        state = RunState(
            run_id=run_id,
            suite=suite,
            profile=profile,
            cmd=cmd,
            cwd=str(_this_dir()),
            log_path=str(log_path),
            started_at=_now_iso(),
        )
        _RUNS[run_id] = state

        try:
            logf = open(log_path, "ab", buffering=0)
            proc = subprocess.Popen(
                cmd,
                cwd=str(_this_dir()),
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            state.proc = proc
            state.pid = proc.pid
            state.status = "running"
            _ACTIVE_BY_SUITE[suite] = run_id
            logger.info("Started %s suite profile=%s pid=%s run_id=%s", suite, profile, proc.pid, run_id)
        except Exception as e:
            state.status = "failed"
            state.error = str(e)
            state.ended_at = _now_iso()
            return {"status": "error", "error": str(e), "run": state.to_public()}

        return {"status": "success", "run": state.to_public()}


def _terminate_locked(run: RunState, force: bool = False) -> None:
    if run.proc is None or run.status not in ("starting", "running"):
        return
    try:
        if force:
            run.proc.kill()
        else:
            run.proc.terminate()
            try:
                run.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                run.proc.kill()
    except Exception as e:
        run.error = str(e)
    finally:
        rc = run.proc.poll()
        run.returncode = rc
        run.status = "stopped" if (rc is None or rc < 0 or rc == 0) else "failed"
        run.ended_at = _now_iso()
        if _ACTIVE_BY_SUITE.get(run.suite) == run.run_id:
            _ACTIVE_BY_SUITE.pop(run.suite, None)


def _coerce_int(v: Optional[int], name: str) -> List[str]:
    return [] if v is None else [f"--{name}", str(int(v))]


def _coerce_bool(flag: bool, name: str) -> List[str]:
    return [f"--{name}"] if flag else []


@mcp.tool()
def list_tools_overview() -> Dict[str, Any]:
    """List available suite profiles and MCP tool usage hints."""
    return {
        "status": "success",
        "suites": {
            "tc": ["segregate", "partition", "shaper", "codel", "ecn", "osi_codel", "all"],
            "slice": ["monitor", "static", "nvs-rate", "nvs-cap", "edf", "all"],
            "kpm_rc": ["kpm", "rc", "both"],
        },
        "notes": [
            "Launch this MCP server from the working conda environment used for xApp scripts.",
            "Suite logs are written to /tmp/flexric_mcp_runs by default.",
            "Only one active run per suite type is allowed (tc/slice/kpm_rc).",
        ],
    }


@mcp.tool()
def tc_start(
    profile: str,
    duration_s: int = 180,
    src_port: Optional[int] = None,
    dst_port: Optional[int] = None,
    protocol: Optional[int] = None,
    pcr_drb_sz: Optional[int] = None,
    codel_interval_ms: Optional[int] = None,
    codel_target_ms: Optional[int] = None,
    shaper_id: Optional[int] = None,
    shaper_window_ms: Optional[int] = None,
    shaper_rate_kbps: Optional[int] = None,
    monitor_rlc: bool = False,
    stop_existing: bool = True,
) -> Dict[str, Any]:
    """Start TC suite profile as a background subprocess and return run metadata."""
    args: List[str] = ["--duration-s", str(int(duration_s))]
    args += _coerce_int(src_port, "src-port")
    args += _coerce_int(dst_port, "dst-port")
    args += _coerce_int(protocol, "protocol")
    args += _coerce_int(pcr_drb_sz, "pcr-drb-sz")
    args += _coerce_int(codel_interval_ms, "codel-interval-ms")
    args += _coerce_int(codel_target_ms, "codel-target-ms")
    args += _coerce_int(shaper_id, "shaper-id")
    args += _coerce_int(shaper_window_ms, "shaper-window-ms")
    args += _coerce_int(shaper_rate_kbps, "shaper-rate-kbps")
    args += _coerce_bool(monitor_rlc, "monitor-rlc")
    return _spawn_suite("tc", profile, args, stop_existing=stop_existing)


@mcp.tool()
def slice_start(
    profile: str = "monitor",
    duration_s: int = 180,
    json_out: str = "rt_slice_stats.json",
    verbose: bool = False,
    assoc_dl_id: Optional[int] = None,
    stop_existing: bool = True,
) -> Dict[str, Any]:
    """Start Slice suite profile and keep logs/JSON output on disk."""
    args: List[str] = ["--duration-s", str(int(duration_s)), "--json-out", str(json_out)]
    args += _coerce_bool(verbose, "verbose")
    args += _coerce_int(assoc_dl_id, "assoc-dl-id")
    return _spawn_suite("slice", profile, args, stop_existing=stop_existing)


@mcp.tool()
def kpm_rc_start(
    profile: str = "kpm",
    period_ms: int = 1000,
    duration_s: int = 180,
    kpm_metrics: str = "rru",
    stop_existing: bool = True,
) -> Dict[str, Any]:
    """Start KPM/RC suite profile."""
    args = [
        "--period-ms", str(int(period_ms)),
        "--duration-s", str(int(duration_s)),
        "--kpm-metrics", str(kpm_metrics),
    ]
    return _spawn_suite("kpm_rc", profile, args, stop_existing=stop_existing)


@mcp.tool()
def runs_list(active_only: bool = False) -> Dict[str, Any]:
    """List known suite runs with status."""
    with _LOCK:
        _poll_locked()
        items = [r.to_public() for r in _RUNS.values()]
    if active_only:
        items = [r for r in items if r["status"] in ("starting", "running")]
    items.sort(key=lambda x: x["started_at"] or "")
    return {"status": "success", "count": len(items), "runs": items}


@mcp.tool()
def run_status(run_id: Optional[str] = None, suite: Optional[str] = None) -> Dict[str, Any]:
    """Get status for a run_id or current active run for a suite."""
    with _LOCK:
        _poll_locked()
        rid = run_id
        if rid is None and suite is not None:
            rid = _ACTIVE_BY_SUITE.get(suite)
        if rid is None:
            return {"status": "error", "error": "Provide run_id or suite"}
        run = _RUNS.get(rid)
        if run is None:
            return {"status": "error", "error": f"Unknown run_id '{rid}'"}
        return {"status": "success", "run": run.to_public()}


@mcp.tool()
def run_log_tail(run_id: Optional[str] = None, suite: Optional[str] = None, lines: int = 80) -> Dict[str, Any]:
    """Return tail of a run log. Provide run_id or suite (uses active run for suite)."""
    with _LOCK:
        _poll_locked()
        rid = run_id if run_id else (_ACTIVE_BY_SUITE.get(suite) if suite else None)
        if rid is None:
            return {"status": "error", "error": "Provide run_id or suite"}
        run = _RUNS.get(rid)
        if run is None:
            return {"status": "error", "error": f"Unknown run_id '{rid}'"}
        tail = _tail_file(run.log_path, lines=lines)
        return {"status": "success", "run": run.to_public(), "tail": tail}


@mcp.tool()
def run_stop(run_id: Optional[str] = None, suite: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Stop a running suite process by run_id or active suite name."""
    with _LOCK:
        _poll_locked()
        rid = run_id if run_id else (_ACTIVE_BY_SUITE.get(suite) if suite else None)
        if rid is None:
            return {"status": "error", "error": "Provide run_id or suite"}
        run = _RUNS.get(rid)
        if run is None:
            return {"status": "error", "error": f"Unknown run_id '{rid}'"}
        _terminate_locked(run, force=force)
        return {"status": "success", "run": run.to_public()}


@mcp.tool()
def stop_all(force: bool = False) -> Dict[str, Any]:
    """Stop all active suite subprocesses."""
    stopped: List[Dict[str, Any]] = []
    with _LOCK:
        _poll_locked()
        active_runs = [r for r in _RUNS.values() if r.status in ("starting", "running")]
        for run in active_runs:
            _terminate_locked(run, force=force)
            stopped.append(run.to_public())
    return {"status": "success", "stopped": stopped, "count": len(stopped)}


@mcp.tool()
def health() -> Dict[str, Any]:
    """MCP server health + active suite runs."""
    with _LOCK:
        _poll_locked()
        active = {
            suite: _RUNS[rid].to_public()
            for suite, rid in _ACTIVE_BY_SUITE.items()
            if rid in _RUNS
        }
        python_cmd = _python_cmd()
    return {
        "status": "success",
        "server": "flexric-suites",
        "cwd": str(_this_dir()),
        "python_cmd": python_cmd,
        "log_root": str(_log_root()),
        "active": active,
        "known_runs": len(_RUNS),
    }


@mcp.tool()
def tc_profiles() -> Dict[str, Any]:
    """Return TC profile descriptions."""
    return {
        "status": "success",
        "profiles": {
            "segregate": "Add FIFO queue + generic OSI classifier to queue 1",
            "partition": "BDP/PCR pacing + two FIFO queues + classifier by src-port",
            "shaper": "Three queues + src-port classifiers + shaper on queue",
            "codel": "BDP/PCR pacing + CoDel queue + classifier",
            "ecn": "BDP/PCR pacing + ECN queue + classifier",
            "osi_codel": "BDP/PCR pacing + CoDel + OSI classifier (dst-port/protocol)",
            "all": "Combined demo (BDP/PCR + CoDel + OSI classifier), optionally RLC monitoring",
        },
    }


@mcp.tool()
def slice_profiles() -> Dict[str, Any]:
    """Return Slice profile descriptions."""
    return {
        "status": "success",
        "profiles": {
            "monitor": "Subscribe to SLICE indications only",
            "static": "Apply STATIC slice configuration and monitor",
            "nvs-rate": "Apply NVS RATE slice configuration and monitor",
            "nvs-cap": "Apply NVS CAPACITY slice configuration and monitor",
            "edf": "Apply EDF slice configuration and monitor",
            "all": "Static add -> UE association attempt -> delete slice id 5 -> monitor",
        },
    }


@mcp.tool()
def slice_custom_config_capabilities() -> Dict[str, Any]:
    """Describe whether this suites MCP server supports arbitrary create_slices-style custom configs."""
    return {
        "status": "success",
        "supported": False,
        "tool_name": "create_slices",
        "server": "flexric-suites",
        "reason": (
            "mcp_flexric_suites.py exposes profile-based slice operations "
            "(slice_start / slice_monitor_check / slice_apply_profile_and_verify), "
            "not arbitrary create_slices(config=...) control messages."
        ),
        "supported_alternatives": [
            "slice_apply_profile_and_verify(profile='static'|'nvs-rate'|'nvs-cap'|'edf'|'all', ...)",
            "slice_start(profile='monitor'|...)",
        ],
        "preserved_custom_fields_note": (
            "Custom fields like per-slice id/label/pos ranges can be validated in the UI/agent, "
            "but cannot be applied by this MCP server unless a dedicated custom-config tool is added."
        ),
    }


@mcp.tool()
def kpm_rc_profiles() -> Dict[str, Any]:
    """Return KPM/RC profile descriptions."""
    return {
        "status": "success",
        "profiles": {
            "kpm": "KPM auto-monitor (RRU/UE/all output filter)",
            "rc": "RC scaffold (prints RC limitations and node info; no RC auto-sub helper yet)",
            "both": "Run KPM monitor and RC scaffold together",
        },
    }


if __name__ == "__main__":
    mcp.run()
