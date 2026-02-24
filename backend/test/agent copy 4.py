#!/usr/bin/env python3
"""
agent.py - AgentRIC (Ollama + MCP)

Target environment:
- langchain-mcp-adapters == 0.1.0
- Option B: connect to MCP Inspector proxy over HTTP (127.0.0.1:6277)
- Auth: Authorization: Bearer <token>
- Also supports stdio fallback (MCP_TRANSPORT=stdio)

Fixes:
- No asyncio.run() inside running loop (uses AnyIO BlockingPortal correctly)
- MultiServerMCPClient is NOT used as a context manager (0.1.0 limitation)
- args_json can be dict OR string OR empty
- ToolRuntime excluded from schema via InjectedToolArg (prevents Pydantic CallableSchema)
- Better ExceptionGroup / TaskGroup formatting
"""

import os
import json
import time
import threading
import contextlib
import atexit
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import anyio
from dotenv import load_dotenv
from typing_extensions import Annotated

from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import ToolMessage

# langchain-mcp-adapters 0.1.0
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools, InjectedToolArg

load_dotenv()

# -------------------------
# Ollama
# -------------------------
HOST = os.getenv("OLLAMA_HOST", "130.233.158.22").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{HOST}:11434").strip()
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:latest").strip()

# -------------------------
# MCP connection selection
# -------------------------
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "http").strip().lower()  # http | stdio
MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "flexric").strip() or "flexric"

# Option B: HTTP proxy (Inspector proxy)
# IMPORTANT:
# - Some setups want full endpoint ".../mcp"
# - Others accept base URL "http://127.0.0.1:6277"
MCP_PROXY_URL = os.getenv("MCP_PROXY_URL", "http://127.0.0.1:6277/mcp").strip()
MCP_PROXY_AUTH_TOKEN = os.getenv("MCP_PROXY_AUTH_TOKEN", "").strip()

# stdio fallback
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", "python3").strip()
MCP_SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", "").strip()

# -------------------------
# Guardrails
# -------------------------
E2_NODE_ALLOWLIST = {x.strip() for x in os.getenv("E2_NODE_ALLOWLIST", "").split(",") if x.strip()}
RATE_LIMIT_MAX_CALLS_PER_MIN = int(os.getenv("RATE_LIMIT_MAX_CALLS_PER_MIN", "60"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "10"))
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in ("1", "true", "yes")
PRB_POS_LOW = int(os.getenv("PRB_POS_LOW", "0"))
PRB_POS_HIGH = int(os.getenv("PRB_POS_HIGH", "273"))
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "./agentRIC_audit.log")


# ============================================================
# Runtime Context + State
# ============================================================
@dataclass(frozen=True)
class Context:
    user_id: str
    session_id: str


class CustomState(AgentState):
    tool_calls: int
    tool_errors: list[str]
    audit: list[dict]
    last_tools: list[dict]
    did_list_tools: bool
    mcp_tools: list[dict]


# ============================================================
# Helpers
# ============================================================
def _to_jsonable(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if hasattr(obj, "content"):
        return _to_jsonable(getattr(obj, "content"))
    return str(obj)


def _extract_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result

    if isinstance(result, ToolMessage):
        try:
            return json.loads(result.content)
        except Exception:
            return {"raw": result.content}

    if hasattr(result, "content"):
        c = getattr(result, "content")
        if isinstance(c, str):
            try:
                return json.loads(c)
            except Exception:
                return {"raw": c}
        return _to_jsonable(c)

    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:
            return {"raw": result}

    return _to_jsonable(result)


def _format_exception(e: BaseException) -> str:
    # Python 3.11 ExceptionGroup / AnyIO TaskGroup
    if hasattr(e, "exceptions") and isinstance(getattr(e, "exceptions"), (list, tuple)):
        parts = [f"{type(e).__name__}: {e}"]
        for i, sub in enumerate(getattr(e, "exceptions"), 1):
            parts.append(f"  [{i}] {type(sub).__name__}: {sub}")
        return "\n".join(parts)
    return f"{type(e).__name__}: {e}"


def _server_cmd_list() -> List[str]:
    cmd = [MCP_SERVER_CMD]
    if MCP_SERVER_ARGS:
        cmd += MCP_SERVER_ARGS.split()
    return cmd


# ============================================================
# AnyIO Portal (correctly entered)
# - Avoid asyncio.run() conflicts in LangGraph dev / async servers
# ============================================================
class _Portal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cm: Optional[contextlib.AbstractContextManager] = None
        self._portal: Optional[anyio.from_thread.BlockingPortal] = None

    def ensure(self) -> anyio.from_thread.BlockingPortal:
        with self._lock:
            if self._portal is not None:
                return self._portal
            self._cm = anyio.from_thread.start_blocking_portal()
            self._portal = self._cm.__enter__()  # IMPORTANT: .call is available only after entering
            return self._portal

    def close(self) -> None:
        with self._lock:
            if self._cm is not None:
                try:
                    self._cm.__exit__(None, None, None)
                except Exception:
                    pass
                self._cm = None
                self._portal = None


_PORTAL = _Portal()
atexit.register(_PORTAL.close)


# ============================================================
# MCP Bridge (0.1.0 compatible)
#   - client = MultiServerMCPClient(server_cfg)
#   - async with client.session(server_name) as session:
#         tools = await load_mcp_tools(session)
# ============================================================
class MCPBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Optional[MultiServerMCPClient] = None
        self._tools_cache: Optional[List[Any]] = None
        self._tools_meta_cache: Optional[List[dict]] = None

    def _build_client(self) -> MultiServerMCPClient:
        if MCP_TRANSPORT == "http":
            if not MCP_PROXY_URL:
                raise RuntimeError("MCP_PROXY_URL is empty (expected http://127.0.0.1:6277 or .../mcp).")

            headers = {}
            if MCP_PROXY_AUTH_TOKEN:
                headers["Authorization"] = f"Bearer {MCP_PROXY_AUTH_TOKEN}"

            server_cfg = {
                MCP_SERVER_NAME: {
                    "transport": "http",
                    "url": MCP_PROXY_URL,
                    "headers": headers,  # accepted by your installed version
                }
            }
            return MultiServerMCPClient(server_cfg)

        # stdio fallback
        cmd = _server_cmd_list()
        if len(cmd) < 2:
            raise RuntimeError("MCP_SERVER_ARGS is empty. Set MCP_SERVER_ARGS to your MCP server script path.")
        server_cfg = {
            MCP_SERVER_NAME: {
                "transport": "stdio",
                "command": cmd[0],
                "args": cmd[1:],
            }
        }
        return MultiServerMCPClient(server_cfg)

    def _ensure_client(self) -> None:
        with self._lock:
            if self._client is None:
                self._client = self._build_client()

    async def _aload_tools(self) -> List[Any]:
        self._ensure_client()
        assert self._client is not None

        async with self._client.session(MCP_SERVER_NAME) as session:
            tools = await load_mcp_tools(session)
        return tools

    def load_tools(self) -> List[Any]:
        with self._lock:
            if self._tools_cache is not None:
                return self._tools_cache

        portal = _PORTAL.ensure()
        try:
            tools = portal.call(self._aload_tools)
        except Exception as e:
            raise RuntimeError(f"Failed to load MCP tools: {_format_exception(e)}") from e

        with self._lock:
            self._tools_cache = tools
            self._tools_meta_cache = None
        return tools

    def list_tools(self) -> List[dict]:
        with self._lock:
            if self._tools_meta_cache is not None:
                return self._tools_meta_cache

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
        with self._lock:
            self._tools_meta_cache = meta
        return meta

    def call_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        tools = self.load_tools()
        tmap = {getattr(t, "name", ""): t for t in tools if getattr(t, "name", None)}
        if tool_name not in tmap:
            raise ValueError(f"Unknown MCP tool: {tool_name}. Call mcp_list_tools first.")
        return tmap[tool_name].invoke(args)

    def reset_cache(self) -> None:
        with self._lock:
            self._tools_cache = None
            self._tools_meta_cache = None


_MCP = MCPBridge()


# ============================================================
# Agent tools (runtime excluded from schema)
# ============================================================
@tool
def mcp_list_tools(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """List tools exposed by the MCP server connected via Inspector proxy (HTTP) or stdio."""
    try:
        tools = _MCP.list_tools()
    except Exception as e:
        return {"status": "error", "tool": "mcp_list_tools", "error": _format_exception(e)}

    st = getattr(runtime, "state", None)
    if isinstance(st, dict):
        st["did_list_tools"] = True
        st["mcp_tools"] = tools

    return {"status": "success", "count": len(tools), "tools": tools}


@tool
def mcp_call_tool(
    tool_name: str,
    args: Optional[dict] = None,
    args_json: Any = "",
    runtime: Annotated[Any, InjectedToolArg] = None,
) -> dict:
    """Call an MCP tool by name. Accepts args as dict OR args_json as JSON string (or dict)."""
    call_args: Dict[str, Any] = {}

    if isinstance(args, dict):
        call_args = args
    elif isinstance(args_json, dict):
        # some callers pass {} instead of a string
        call_args = args_json
    elif isinstance(args_json, str) and args_json.strip():
        try:
            decoded = json.loads(args_json)
            if not isinstance(decoded, dict):
                return {"status": "error", "tool": tool_name, "error": "args_json must decode to a JSON object"}
            call_args = decoded
        except Exception as e:
            return {"status": "error", "tool": tool_name, "error": f"Invalid args_json: {_format_exception(e)}"}

    try:
        raw = _MCP.call_tool(tool_name, call_args)
        payload = _extract_payload(raw)
        return {
            "status": "success",
            "tool": tool_name,
            "args": _to_jsonable(call_args),
            "result": _to_jsonable(payload),
        }
    except Exception as e:
        return {
            "status": "error",
            "tool": tool_name,
            "args": _to_jsonable(call_args),
            "error": _format_exception(e),
        }


@tool
def mcp_reset_tools_cache(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """Reset cached MCP tool list (useful if MCP server restarted or tools changed)."""
    _MCP.reset_cache()
    return {"status": "success", "message": "MCP tools cache reset. Call mcp_list_tools again."}


@tool
def session_info(runtime: Annotated[Any, InjectedToolArg]) -> dict:
    """Return session context and recent tool error summary."""
    ctx = getattr(runtime, "context", None)
    state = getattr(runtime, "state", {}) or {}
    return {
        "status": "success",
        "context": {"user_id": getattr(ctx, "user_id", ""), "session_id": getattr(ctx, "session_id", "")},
        "state": {
            "tool_calls": state.get("tool_calls", 0),
            "did_list_tools": state.get("did_list_tools", False),
            "tool_errors": state.get("tool_errors", [])[-5:],
        },
    }


TOOLS = [mcp_list_tools, mcp_call_tool, mcp_reset_tools_cache, session_info]


# ============================================================
# Prompt
# ============================================================
BASE_SYSTEM_PROMPT = """You are AgentRIC, an assistant for O-RAN near-RT RIC operations via FlexRIC tools exposed through MCP.

Follow ReAct:
- Reasoning: one short sentence about next step
- Acting: call tools only when needed
- Observation: use tool results
- Final: concise answer

Rules:
- If you don't know tool names, call mcp_list_tools first.
- Never invent E2 node IDs / slice IDs / RNTIs / cell IDs / PRB values.
- Prefer read/observe tools before control tools.
- If DRY_RUN is enabled, avoid control actions and explain what you would do.
"""


# ============================================================
# Middleware (rate limit + allowlist + dryrun + audit)
# ============================================================
class AgentRICMiddleware(AgentMiddleware):
    def __init__(self) -> None:
        self._window_start = 0.0
        self._window_calls = 0

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self._prepare_request(request))

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> ModelResponse:
        return await handler(self._prepare_request(request))

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        state = request.state or {}
        runtime = request.runtime

        ctx = getattr(runtime, "context", None)
        ctx_line = f"\nContext: user_id={getattr(ctx,'user_id','')}, session_id={getattr(ctx,'session_id','')}\n" if ctx else ""

        did_list = bool(state.get("did_list_tools", False))
        prompt = BASE_SYSTEM_PROMPT + ctx_line

        if not did_list:
            prompt += "\nNote: You have not listed MCP tools yet. Use mcp_list_tools first.\n"
        if DRY_RUN:
            prompt += "\nDRY_RUN is enabled: do NOT perform changes; prefer observation/read operations.\n"
        if E2_NODE_ALLOWLIST:
            prompt += f"\nE2 allowlist enabled: only use e2_node_id in {sorted(E2_NODE_ALLOWLIST)}.\n"

        return request.override(system_prompt=prompt, tools=request.tools)

    def wrap_tool_call(self, request, handler):
        return self._run_tool(request, handler, is_async=False)

    async def awrap_tool_call(self, request, handler):
        return await self._run_tool(request, handler, is_async=True)

    def _audit(self, runtime: Any, record: dict) -> None:
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st.setdefault("audit", [])
            if isinstance(st["audit"], list):
                st["audit"].append(record)
        try:
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _rate_limit_ok(self) -> bool:
        now = time.time()
        if now - self._window_start >= 60:
            self._window_start = now
            self._window_calls = 0
        self._window_calls += 1
        if self._window_calls > RATE_LIMIT_MAX_CALLS_PER_MIN:
            return False
        if self._window_calls > RATE_LIMIT_BURST and (now - self._window_start) < 5:
            return False
        return True

    def _extract_inner_mcp(self, request) -> Optional[Tuple[str, dict]]:
        if request.tool_call.get("name") != "mcp_call_tool":
            return None
        outer_args = request.tool_call.get("args", {}) or {}
        inner_tool = outer_args.get("tool_name")
        if not isinstance(inner_tool, str) or not inner_tool:
            return None

        inner_args: Dict[str, Any] = {}
        # args can be passed directly or via args_json
        if isinstance(outer_args.get("args"), dict):
            inner_args = outer_args["args"]
        else:
            aj = outer_args.get("args_json", "")
            if isinstance(aj, dict):
                inner_args = aj
            elif isinstance(aj, str) and aj.strip():
                try:
                    decoded = json.loads(aj)
                    if isinstance(decoded, dict):
                        inner_args = decoded
                except Exception:
                    inner_args = {}
        return inner_tool, inner_args

    def _guard_mcp(self, tool_name: str, tool_args: dict) -> Optional[str]:
        if E2_NODE_ALLOWLIST:
            e2 = tool_args.get("e2_node_id") or tool_args.get("e2NodeId") or tool_args.get("node_id")
            if isinstance(e2, str) and e2 and e2 not in E2_NODE_ALLOWLIST:
                return f"Blocked: e2_node_id '{e2}' not in allowlist."

        pos_low = tool_args.get("pos_low")
        pos_high = tool_args.get("pos_high")
        if isinstance(pos_low, int) and isinstance(pos_high, int):
            if pos_low < PRB_POS_LOW or pos_high > PRB_POS_HIGH or pos_low > pos_high:
                return f"Blocked: PRB range invalid (pos_low={pos_low}, pos_high={pos_high}) bounds [{PRB_POS_LOW},{PRB_POS_HIGH}]."

        if DRY_RUN:
            lowered = tool_name.lower()
            controlish = any(k in lowered for k in ["control", "set", "write", "handover", "allocate", "prb", "configure"])
            if controlish:
                return f"Blocked by DRY_RUN: refusing control-like tool '{tool_name}'."

        return None

    async def _run_tool(self, request, handler, is_async: bool):
        runtime = request.runtime
        tool_name = request.tool_call.get("name")
        tool_args = request.tool_call.get("args", {})

        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st["tool_calls"] = int(st.get("tool_calls", 0)) + 1
            st.setdefault("last_tools", [])
            if isinstance(st["last_tools"], list):
                st["last_tools"].append({"name": tool_name, "args": _to_jsonable(tool_args), "ts": time.time()})

        if not self._rate_limit_ok():
            msg = "Rate limit exceeded."
            return ToolMessage(content=json.dumps({"status": "error", "tool": tool_name, "message": msg}),
                               tool_call_id=request.tool_call.get("id", ""))

        inner = self._extract_inner_mcp(request)
        if inner:
            inner_tool, inner_args = inner
            blocked = self._guard_mcp(inner_tool, inner_args)
            if blocked:
                self._audit(runtime, {"ts": time.time(), "kind": "blocked", "inner_tool": inner_tool, "inner_args": _to_jsonable(inner_args), "reason": blocked})
                return ToolMessage(content=json.dumps({"status": "blocked", "tool": inner_tool, "message": blocked}),
                                   tool_call_id=request.tool_call.get("id", ""))

        self._audit(runtime, {"ts": time.time(), "kind": "call", "tool": tool_name, "args": _to_jsonable(tool_args)})

        try:
            if is_async:
                return await handler(request)
            return handler(request)
        except Exception as e:
            err = _format_exception(e)
            self._audit(runtime, {"ts": time.time(), "kind": "error", "tool": tool_name, "args": _to_jsonable(tool_args), "error": err})
            return ToolMessage(content=json.dumps({"status": "error", "tool": tool_name, "message": err}),
                               tool_call_id=request.tool_call.get("id", ""))


class PersistToolResultsMiddleware(AgentMiddleware):
    def wrap_tool_call(self, request, handler):
        result = handler(request)
        self._maybe_persist(request, result)
        return result

    async def awrap_tool_call(self, request, handler):
        result = await handler(request)
        self._maybe_persist(request, result)
        return result

    def _maybe_persist(self, request, result):
        runtime = request.runtime
        st = getattr(runtime, "state", None)
        if not isinstance(st, dict):
            return
        name = request.tool_call.get("name")
        payload = _extract_payload(result)
        if name == "mcp_list_tools" and isinstance(payload, dict):
            st["did_list_tools"] = True
            tools = payload.get("tools")
            if isinstance(tools, list):
                st["mcp_tools"] = tools


# ============================================================
# Model + Agent graph
# ============================================================
model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
    middleware=[AgentRICMiddleware(), PersistToolResultsMiddleware()],
)

__all__ = ["agent", "Context", "CustomState"]
