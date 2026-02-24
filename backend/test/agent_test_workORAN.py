#!/usr/bin/env python3
"""
agent.py - minimal AgentRIC (Ollama + MCP transport auto/http/stdio)

Unified FlexRIC suites variant:
- Keeps MCP client object alive (prevents session teardown/disconnect churn).
- Supports HTTP or stdio MCP transport from env.
- Defaults to stdio MCP suites server (mcp_flexric_suites.py).
- Calls async-only MCP tools safely via ainvoke() first.
"""

import os
import sys
import shlex
import threading
import contextlib
import inspect
import json
import queue
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import anyio
from dotenv import load_dotenv
from typing_extensions import Annotated

from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain.agents import create_agent, AgentState

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools, InjectedToolArg

load_dotenv()

# -------------------------
# Ollama
# -------------------------
HOST = os.getenv("OLLAMA_HOST", "127.0.0.1").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{HOST}:11434").strip()
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:latest").strip()

# -------------------------
# MCP transport
# -------------------------
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()  # auto | http | stdio
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", sys.executable).strip()
_DEFAULT_SUITES_SERVER = "/home/dang/flexric/build/examples/xApp/python3/mcp_flexric_suites.py"
MCP_SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", _DEFAULT_SUITES_SERVER).strip()
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "flexric-suites").strip() or "flexric-suites"
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", os.getenv("MCP_PROXY_URL", "http://127.0.0.1:8000/mcp")).strip()
MCP_HTTP_AUTH_TOKEN = os.getenv("MCP_HTTP_AUTH_TOKEN", os.getenv("MCP_PROXY_AUTH_TOKEN", "")).strip()
MCP_CALL_TIMEOUT_S = int(os.getenv("MCP_CALL_TIMEOUT_S", "120").strip() or "120")


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
    return os.path.join(os.path.dirname(__file__), "agentRIC_audit.log")


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
        with open(_audit_log_path(), "a", encoding="utf-8") as f:
            f.write(_safe_json(rec, max_len=6000) + "\n")
    except Exception:
        # Logging must never break agent execution.
        pass


def _require_int(value: Any, field: str) -> Optional[str]:
    if isinstance(value, bool):
        return f"{field} must be an integer, not boolean"
    try:
        int(value)
    except Exception:
        return f"{field} must be an integer"
    return None


def _require_number(value: Any, field: str) -> Optional[str]:
    if isinstance(value, bool):
        return f"{field} must be a number, not boolean"
    try:
        float(value)
    except Exception:
        return f"{field} must be a number"
    return None


def _validate_slice_config(config: Any) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(config, dict):
        return {"ok": False, "errors": ["config must be a JSON object"], "warnings": warnings}

    algo = str(config.get("slice_sched_algo", "")).strip().upper()
    if algo not in {"STATIC", "NVS", "EDF"}:
        errors.append("config.slice_sched_algo must be one of: STATIC, NVS, EDF")

    slices = config.get("slices")
    if not isinstance(slices, list) or len(slices) == 0:
        errors.append("config.slices must be a non-empty array")
        return {"ok": False, "errors": errors, "warnings": warnings}

    seen_ids: set[int] = set()
    static_ranges: List[tuple[int, int, int]] = []
    nvs_cap_sum = 0.0

    for i, sl in enumerate(slices):
        path = f"config.slices[{i}]"
        if not isinstance(sl, dict):
            errors.append(f"{path} must be an object")
            continue

        id_err = _require_int(sl.get("id"), f"{path}.id")
        if id_err:
            errors.append(id_err)
        else:
            sid = int(sl["id"])
            if sid < 0:
                errors.append(f"{path}.id must be >= 0")
            if sid in seen_ids:
                errors.append(f"{path}.id duplicates slice id {sid}")
            seen_ids.add(sid)

        label = sl.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{path}.label must be a non-empty string")

        params = sl.get("slice_algo_params")
        if not isinstance(params, dict):
            errors.append(f"{path}.slice_algo_params must be an object")
            continue

        if algo == "STATIC":
            for k in ("pos_low", "pos_high"):
                err = _require_int(params.get(k), f"{path}.slice_algo_params.{k}")
                if err:
                    errors.append(err)
            if all(_require_int(params.get(k), k) is None for k in ("pos_low", "pos_high")):
                lo, hi = int(params["pos_low"]), int(params["pos_high"])
                if lo < 0 or hi < 0:
                    errors.append(f"{path}.slice_algo_params.pos_low/pos_high must be >= 0")
                if lo > hi:
                    errors.append(f"{path}.slice_algo_params.pos_low must be <= pos_high")
                static_ranges.append((lo, hi, i))

        elif algo == "NVS":
            nvs_type = str(sl.get("type", "RATE")).strip().upper()
            if nvs_type in {"SLICE_SM_NVS_V0_RATE", "RATE"}:
                for k in ("mbps_rsvd", "mbps_ref"):
                    err = _require_int(params.get(k), f"{path}.slice_algo_params.{k}")
                    if err:
                        errors.append(err)
                if all(_require_int(params.get(k), k) is None for k in ("mbps_rsvd", "mbps_ref")):
                    mbps_rsvd = int(params["mbps_rsvd"])
                    mbps_ref = int(params["mbps_ref"])
                    if mbps_rsvd <= 0 or mbps_ref <= 0:
                        errors.append(f"{path}.slice_algo_params.mbps_rsvd/mbps_ref must be > 0")
                    if mbps_rsvd > mbps_ref:
                        errors.append(f"{path}.slice_algo_params.mbps_rsvd must be <= mbps_ref")
            elif nvs_type in {"SLICE_SM_NVS_V0_CAPACITY", "CAPACITY"}:
                err = _require_number(params.get("pct_rsvd"), f"{path}.slice_algo_params.pct_rsvd")
                if err:
                    errors.append(err)
                else:
                    pct = float(params["pct_rsvd"])
                    if pct <= 0 or pct > 1:
                        errors.append(f"{path}.slice_algo_params.pct_rsvd must be in (0, 1]")
                    nvs_cap_sum += pct
            else:
                errors.append(f"{path}.type must be RATE or CAPACITY for NVS")

        elif algo == "EDF":
            for k in ("deadline", "guaranteed_prbs", "max_replenish"):
                err = _require_int(params.get(k), f"{path}.slice_algo_params.{k}")
                if err:
                    errors.append(err)
            if all(_require_int(params.get(k), k) is None for k in ("deadline", "guaranteed_prbs", "max_replenish")):
                deadline = int(params["deadline"])
                guaranteed_prbs = int(params["guaranteed_prbs"])
                max_replenish = int(params["max_replenish"])
                if deadline <= 0:
                    errors.append(f"{path}.slice_algo_params.deadline must be > 0")
                if guaranteed_prbs < 0:
                    errors.append(f"{path}.slice_algo_params.guaranteed_prbs must be >= 0")
                if max_replenish < 0:
                    errors.append(f"{path}.slice_algo_params.max_replenish must be >= 0")

    if algo == "STATIC":
        ordered = sorted(static_ranges, key=lambda t: t[0])
        for idx in range(1, len(ordered)):
            prev_lo, prev_hi, prev_i = ordered[idx - 1]
            cur_lo, cur_hi, cur_i = ordered[idx]
            if cur_lo <= prev_hi:
                warnings.append(
                    f"STATIC ranges overlap between slices[{prev_i}] ({prev_lo}-{prev_hi}) and slices[{cur_i}] ({cur_lo}-{cur_hi})"
                )

    if algo == "NVS" and nvs_cap_sum > 1.0 + 1e-9:
        errors.append(f"Sum of NVS CAPACITY pct_rsvd values must be <= 1.0 (got {nvs_cap_sum:.3f})")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


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


def _validate_slice_profile(profile: str) -> Optional[str]:
    allowed = {"monitor", "static", "nvs-rate", "nvs-cap", "edf", "all"}
    if profile not in allowed:
        return f"Invalid slice profile '{profile}'. Allowed: {', '.join(sorted(allowed))}."
    return None


def _validate_int_range(name: str, value: int, min_v: int, max_v: int) -> Optional[str]:
    if value < min_v or value > max_v:
        return f"{name}={value} is out of range [{min_v}, {max_v}]."
    return None


def _clamp_int(value: int, min_v: int, max_v: int) -> int:
    return max(min_v, min(max_v, value))


def _looks_like_kpm_line(line: str) -> bool:
    s = line.lower()
    if "meas=" in s:
        return True
    return ("kpm" in s and ("indication" in s or "metric" in s or "rru" in s or "ue" in s))


def _extract_kpm_indication_lines(lines: List[str], max_items: int = 12) -> List[str]:
    picked: List[str] = []
    seen: set[str] = set()
    for line in reversed(lines):
        text = str(line).strip()
        if not text or not _looks_like_kpm_line(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        picked.append(text)
        if len(picked) >= max_items:
            break
    picked.reverse()
    return picked


def _validate_slice_start_inputs(profile: str, duration_s: int, assoc_dl_id: Optional[int] = None) -> Optional[str]:
    msg = _validate_slice_profile(profile)
    if msg:
        return msg
    msg = _validate_int_range("duration_s", int(duration_s), 1, 3600)
    if msg:
        return msg
    if assoc_dl_id is not None:
        msg = _validate_int_range("assoc_dl_id", int(assoc_dl_id), 0, 255)
        if msg:
            return msg
    return None


def _validate_slice_verify_inputs(
    profile: str,
    duration_s: int,
    startup_timeout_s: int,
    verify_tail_lines: int,
    assoc_dl_id: Optional[int] = None,
) -> Optional[str]:
    msg = _validate_slice_start_inputs(profile, duration_s, assoc_dl_id=assoc_dl_id)
    if msg:
        return msg
    msg = _validate_int_range("startup_timeout_s", int(startup_timeout_s), 1, 120)
    if msg:
        return msg
    msg = _validate_int_range("verify_tail_lines", int(verify_tail_lines), 20, 1000)
    if msg:
        return msg
    return None


# ============================================================
# MCP Bridge
# ============================================================
class MCPBridge:
    """
    Spawns MCP stdio server and loads tools once (cached).
    Uses AnyIO BlockingPortal to call async tool.ainvoke() from sync code.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._portal_cm: Optional[contextlib.AbstractContextManager] = None
        self._portal: Optional[anyio.from_thread.BlockingPortal] = None
        self._client: Optional[MultiServerMCPClient] = None
        self._tools_cache: Optional[list] = None
        self._tool_map_cache: Optional[Dict[str, Any]] = None

    def _effective_transport(self) -> str:
        if MCP_TRANSPORT in ("http", "stdio"):
            return MCP_TRANSPORT
        if MCP_TRANSPORT != "auto":
            raise RuntimeError("MCP_TRANSPORT must be one of: auto, http, stdio")
        if MCP_HTTP_URL and ("MCP_HTTP_URL" in os.environ or "MCP_PROXY_URL" in os.environ):
            return "http"
        return "stdio"

    def _build_client(self) -> MultiServerMCPClient:
        transport = self._effective_transport()
        if transport == "http":
            if not MCP_HTTP_URL:
                raise RuntimeError("MCP_HTTP_URL is empty.")
            headers: Dict[str, str] = {}
            if MCP_HTTP_AUTH_TOKEN:
                headers["Authorization"] = f"Bearer {MCP_HTTP_AUTH_TOKEN}"
            server_cfg = {
                MCP_SERVER_NAME: {
                    "transport": "http",
                    "url": MCP_HTTP_URL,
                    "headers": headers,
                }
            }
            return MultiServerMCPClient(server_cfg)

        cmd = _server_cmd_list()
        if len(cmd) < 2:
            raise RuntimeError("MCP_SERVER_ARGS is empty. Set it to your MCP server script path.")
        server_cfg = {
            MCP_SERVER_NAME: {
                "transport": "stdio",
                "command": cmd[0],
                "args": cmd[1:],
            }
        }
        return MultiServerMCPClient(server_cfg)

    def _ensure_client(self) -> MultiServerMCPClient:
        with self._lock:
            if self._client is None:
                self._client = self._build_client()
            return self._client

    def _ensure_portal(self) -> None:
        with self._lock:
            if self._portal is not None:
                return
            self._portal_cm = anyio.from_thread.start_blocking_portal()
            self._portal = self._portal_cm.__enter__()

    def _portal_call_safe(self, fn, *args):
        """Call portal from sync code with timeout protection on every MCP call."""
        self._ensure_portal()
        assert self._portal is not None
        q: "queue.Queue[tuple[bool, Any]]" = queue.Queue(maxsize=1)

        def _worker():
            try:
                result = self._portal.call(fn, *args)  # type: ignore[union-attr]
                q.put((True, result))
            except Exception as exc:
                q.put((False, exc))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        try:
            ok, payload = q.get(timeout=max(5, MCP_CALL_TIMEOUT_S))
        except queue.Empty as e:
            raise TimeoutError(
                f"MCP call timed out after {MCP_CALL_TIMEOUT_S}s. "
                "The MCP server may be hung or the operation is taking too long."
            ) from e
        t.join(timeout=0.1)
        if ok:
            return payload
        raise payload

    async def _async_load_tools(self) -> list:
        client = self._ensure_client()

        if hasattr(client, "get_tools"):
            return await client.get_tools()

        if hasattr(client, "session"):
            async with client.session(MCP_SERVER_NAME) as session:
                return await load_mcp_tools(session)

        raise RuntimeError(
            "MultiServerMCPClient has neither get_tools() nor session(). "
            "Upgrade: pip install -U langchain-mcp-adapters"
        )

    def load_tools(self) -> list:
        self._ensure_portal()
        assert self._portal is not None

        with self._lock:
            if self._tools_cache is not None:
                return self._tools_cache

        tools = self._portal_call_safe(self._async_load_tools)

        with self._lock:
            self._tools_cache = tools
            self._tool_map_cache = None
        return tools

    def list_tools_meta(self) -> List[dict]:
        tools = self.load_tools()
        out: List[dict] = []
        for t in tools:
            out.append(
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "args_schema": str(getattr(t, "args_schema", "")) if getattr(t, "args_schema", None) else None,
                }
            )
        return out

    def _tool_map(self) -> Dict[str, Any]:
        with self._lock:
            if self._tool_map_cache is not None:
                return self._tool_map_cache

        tools = self.load_tools()
        tmap = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}

        with self._lock:
            self._tool_map_cache = tmap
        return tmap

    async def _async_call(self, tool_name: str, args: Dict[str, Any]) -> Any:
        tmap = self._tool_map()
        if tool_name not in tmap:
            raise ValueError(f"Unknown MCP tool '{tool_name}'. Call mcp_list_tools first.")

        t = tmap[tool_name]

        if hasattr(t, "ainvoke"):
            try:
                return await t.ainvoke(args)
            except Exception as e:
                if not _is_sync_invocation_error(e):
                    raise

        cor = getattr(t, "coroutine", None)
        if callable(cor):
            try:
                sig = inspect.signature(cor)
                if len(sig.parameters) == 1:
                    return await cor(args)
                return await cor(**args)
            except TypeError:
                return await cor(args)

        if hasattr(t, "invoke"):
            try:
                return t.invoke(args)
            except NotImplementedError as e:
                raise RuntimeError(
                    f"Tool '{tool_name}' is async-only and cannot be invoked sync. "
                    "Use adapter path that supports ainvoke()."
                ) from e

        raise RuntimeError(f"Tool object has neither ainvoke() nor invoke(): {type(t)}")

    def call(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        return self._portal_call_safe(self._async_call, tool_name, args or {})


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
        return {
            "status": "error",
            "tool": "mcp_list_tools",
            "error": _format_exc(e),
            "transport": MCP_TRANSPORT,
            "spawn": {"cmd": MCP_SERVER_CMD, "args": MCP_SERVER_ARGS, "name": MCP_SERVER_NAME},
            "http": {"url": MCP_HTTP_URL, "has_auth_token": bool(MCP_HTTP_AUTH_TOKEN)},
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
def mcp_health(runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'health'."""
    try:
        return {"status": "success", "result": _MCP.call("health", {})}
    except Exception as e:
        return {"status": "error", "tool": "health", "error": _format_exc(e)}


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

    # Prefer MCP-native deterministic helper when available.
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

    # Fallback for profile-based flexric suites MCP server.
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
    return {
        "status": "success",
        "state": {
            "did_list_tools": state.get("did_list_tools", False),
            "mcp_tools_count": len(state.get("mcp_tools", []) or []),
        },
        "transport": MCP_TRANSPORT,
        "http": {"url": MCP_HTTP_URL, "has_auth_token": bool(MCP_HTTP_AUTH_TOKEN)},
        "spawn": {"cmd": MCP_SERVER_CMD, "args": MCP_SERVER_ARGS, "name": MCP_SERVER_NAME},
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

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
)

__all__ = ["agent", "Context", "CustomState"]
