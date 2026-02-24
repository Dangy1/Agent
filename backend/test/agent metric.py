#!/usr/bin/env python3
"""
agent.py - minimal AgentRIC (Ollama + MCP transport auto/http/stdio)

Fixes:
- Keep MCP client object alive (prevents session teardown/disconnect churn).
- Support HTTP or stdio MCP transport from env.
- Call async-only MCP tools safely via ainvoke() first.
"""

import os
import shlex
import threading
import contextlib
import inspect
import json
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
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "auto").strip().lower()  # auto | http | stdio
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", "python3").strip()
MCP_SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", "").strip()
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "flexric").strip() or "flexric"
MCP_HTTP_URL = os.getenv("MCP_HTTP_URL", os.getenv("MCP_PROXY_URL", "http://127.0.0.1:8000/mcp")).strip()
MCP_HTTP_AUTH_TOKEN = os.getenv("MCP_HTTP_AUTH_TOKEN", os.getenv("MCP_PROXY_AUTH_TOKEN", "")).strip()


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
    # Python 3.11+ ExceptionGroup (anyio TaskGroup errors show up here)
    if isinstance(e, ExceptionGroup):
        lines = [f"{type(e).__name__}: {e}"]
        for i, sub in enumerate(e.exceptions, 1):
            lines.append(f"  [{i}] {type(sub).__name__}: {sub}")
        return "\n".join(lines)
    return f"{type(e).__name__}: {e}"


def _is_sync_invocation_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return "does not support sync invocation" in msg


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
        # auto: prefer explicit HTTP URL override; otherwise stdio
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

    async def _async_load_tools(self) -> list:
        client = self._ensure_client()

        # Option A
        if hasattr(client, "get_tools"):
            return await client.get_tools()

        # Option B
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

        tools = self._portal.call(self._async_load_tools)

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

        # Primary path for async-capable tools.
        if hasattr(t, "ainvoke"):
            try:
                return await t.ainvoke(args)
            except Exception as e:
                # Some adapter/tool versions expose async-only tools but ainvoke()
                # falls back to sync invoke() internally, which raises here.
                if not _is_sync_invocation_error(e):
                    raise

        # Fallback: call coroutine directly if tool object exposes one.
        cor = getattr(t, "coroutine", None)
        if callable(cor):
            try:
                sig = inspect.signature(cor)
                if len(sig.parameters) == 1:
                    return await cor(args)
                return await cor(**args)
            except TypeError:
                # Signature mismatch fallback.
                return await cor(args)

        # Fallback (rare, sync tools only)
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
        self._ensure_portal()
        assert self._portal is not None
        return self._portal.call(self._async_call, tool_name, args or {})


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
def mcp_start(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """Call MCP server tool 'start'."""
    try:
        res = _MCP.call("start", {})
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
def mcp_get_mac_metrics(mode: str = "summary", runtime: Annotated[Any, InjectedToolArg] = None) -> dict:
    """Call MCP server tool 'get_mac_metrics'."""
    if mode not in ("summary", "raw"):
        return {"status": "error", "error": "mode must be 'summary' or 'raw'"}
    try:
        res = _MCP.call("get_mac_metrics", {"mode": mode})
        return {"status": "success", "mode": mode, "result": res}
    except Exception as e:
        return {
            "status": "error",
            "tool": "get_mac_metrics",
            "mode": mode,
            "error": _format_exc(e),
            "hint": (
                "If stdio: ensure server writes ONLY JSON-RPC to stdout. "
                "If HTTP: keep MCP server process persistent and use MCP_TRANSPORT=http."
            ),
        }


@tool
def mcp_call_tool(
    tool_name: str,
    args: Optional[dict] = None,
    args_json: Any = "",
    runtime: Annotated[Any, InjectedToolArg] = None,
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


TOOLS = [mcp_list_tools, mcp_start, mcp_get_mac_metrics, mcp_call_tool, session_info]


# ============================================================
# Minimal agent
# ============================================================
BASE_SYSTEM_PROMPT = """You are AgentRIC.
Use tools:
- mcp_list_tools to see MCP server tools
- mcp_start to start FlexRIC subscriptions
- mcp_get_mac_metrics(mode="summary") to fetch MAC metrics
- mcp_call_tool(tool_name, args/args_json) for direct MCP tool calls

If a tool fails, report the exact tool error and hint; do not claim chat-interface limitations.
"""

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
)

__all__ = ["agent", "Context", "CustomState"]
