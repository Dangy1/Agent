#!/usr/bin/env python3
"""
agent.py - minimal AgentRIC (Ollama + MCP stdio) for ONLY listing tools

- Works with langchain-mcp-adapters >= 0.1.0
- Spawns MCP stdio server from:
    MCP_SERVER_CMD, MCP_SERVER_ARGS, MCP_SERVER_NAME
- Lists tools only (no guardrails/middleware)
- Uses shlex.split for MCP_SERVER_ARGS (handles quotes safely)
"""

import os
import shlex
import threading
import contextlib
from dataclasses import dataclass
from typing import Any, List, Optional

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
# Ollama (kept; agent still needs a model object for create_agent)
# -------------------------
HOST = os.getenv("OLLAMA_HOST", "127.0.0.1").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{HOST}:11434").strip()
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:latest").strip()

# -------------------------
# MCP stdio server command
# -------------------------
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", "python3").strip()
MCP_SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", "").strip()
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "flexric").strip() or "flexric"


# ============================================================
# Runtime Context + State (minimal)
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
    """
    Builds the stdio spawn command:
      [MCP_SERVER_CMD] + shlex.split(MCP_SERVER_ARGS)

    Example:
      MCP_SERVER_CMD=python3
      MCP_SERVER_ARGS="/home/dang/flexric/.../mcp_flexric_metrics.py"
    """
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


# ============================================================
# MCP Bridge (stdio, adapters >= 0.1.0)
# ============================================================
class MCPBridge:
    """
    Loads MCP tools once (cached) by spawning the MCP server over stdio.
    Uses AnyIO BlockingPortal to call async MCP APIs from sync tool functions.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._portal_cm: Optional[contextlib.AbstractContextManager] = None
        self._portal: Optional[anyio.from_thread.BlockingPortal] = None
        self._tools_cache: Optional[list] = None

    def _ensure_portal(self) -> None:
        with self._lock:
            if self._portal is not None:
                return
            self._portal_cm = anyio.from_thread.start_blocking_portal()
            self._portal = self._portal_cm.__enter__()

    async def _async_load_tools(self) -> list:
        cmd = _server_cmd_list()
        if len(cmd) < 2:
            raise RuntimeError(
                "MCP_SERVER_ARGS is empty. Set MCP_SERVER_ARGS to your MCP server script path."
            )

        server_cfg = {
            MCP_SERVER_NAME: {
                "transport": "stdio",
                "command": cmd[0],
                "args": cmd[1:],
            }
        }

        client = MultiServerMCPClient(server_cfg)

        # Option A (preferred)
        if hasattr(client, "get_tools"):
            return await client.get_tools()

        # Option B
        if hasattr(client, "session"):
            async with client.session(MCP_SERVER_NAME) as session:
                return await load_mcp_tools(session)

        raise RuntimeError(
            "MultiServerMCPClient has neither get_tools() nor session(). "
            "Please upgrade: pip install -U langchain-mcp-adapters"
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
        return tools

    def list_tools_meta(self) -> List[dict]:
        tools = self.load_tools()
        meta: List[dict] = []
        for t in tools:
            meta.append(
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    "args_schema": str(getattr(t, "args_schema", "")) if getattr(t, "args_schema", None) else None,
                }
            )
        return meta


_MCP = MCPBridge()


# ============================================================
# Agent-facing tools (ONLY list tools)
# ============================================================
@tool
def mcp_list_tools(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """List tools exposed by the MCP stdio server (spawned via MCP_SERVER_CMD/MCP_SERVER_ARGS)."""
    try:
        tools = _MCP.list_tools_meta()
    except Exception as e:
        return {
            "status": "error",
            "tool": "mcp_list_tools",
            "error": _format_exc(e),
            "hint": (
                "If this is a stdio MCP server, the client must spawn it. "
                "Do NOT manually start the stdio server in another terminal. "
                "Also ensure the server writes NO non-JSON text to stdout."
            ),
            "spawn": {"cmd": MCP_SERVER_CMD, "args": MCP_SERVER_ARGS, "name": MCP_SERVER_NAME},
        }

    st = getattr(runtime, "state", None)
    if isinstance(st, dict):
        st["did_list_tools"] = True
        st["mcp_tools"] = tools

    return {"status": "success", "count": len(tools), "tools": tools}


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
        "spawn": {"cmd": MCP_SERVER_CMD, "args": MCP_SERVER_ARGS, "name": MCP_SERVER_NAME},
    }


TOOLS = [mcp_list_tools, session_info]


# ============================================================
# Minimal agent
# ============================================================
BASE_SYSTEM_PROMPT = "You are AgentRIC. If the user asks about tools, call mcp_list_tools and return the result."

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
)

__all__ = ["agent", "Context", "CustomState"]