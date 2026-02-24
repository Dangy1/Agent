import os
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import ToolMessage

load_dotenv()

# ============================================================
# Ollama endpoint selection
# ============================================================
HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{HOST}:11434")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:latest")

# ============================================================
# MCP FlexRIC server (stdio) config
# ============================================================
# Example:
# export MCP_SERVER_CMD="python3"
# export MCP_SERVER_ARGS="/home/dang/agent_test/backend/mcp_flexric_server.py"
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", "python3").strip()
MCP_SERVER_ARGS = os.getenv("MCP_SERVER_ARGS", "").strip()

# ============================================================
# Optional "agent-safe" guards (even for non-secured server)
# ============================================================
# Comma-separated allowlist of e2_node_id, e.g.:
# export E2_NODE_ALLOWLIST="gnb1,gnb2,310-410-...-... "
E2_NODE_ALLOWLIST = {
    x.strip() for x in os.getenv("E2_NODE_ALLOWLIST", "").split(",") if x.strip()
}

# Rate limiting (simple in-agent limiter)
RATE_LIMIT_MAX_CALLS_PER_MIN = int(os.getenv("RATE_LIMIT_MAX_CALLS_PER_MIN", "60"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "10"))

# Dry run (block control-ish tools unless explicitly allowed)
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in ("1", "true", "yes")

# Optional PRB bounds (best-effort check; only enforced if args include pos_low/pos_high)
PRB_POS_LOW = int(os.getenv("PRB_POS_LOW", "0"))
PRB_POS_HIGH = int(os.getenv("PRB_POS_HIGH", "273"))  # typical max for 100MHz @ 30k (varies)

# Audit log
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "./agentRIC_audit.log")


# ============================================================
# Runtime Context (immutable)
# ============================================================
@dataclass(frozen=True)
class Context:
    user_id: str
    session_id: str


# ============================================================
# Custom short-term memory (state_schema)
# ============================================================
class CustomState(AgentState):
    tool_calls: int
    tool_errors: list[str]
    audit: list[dict]
    last_tools: list[dict]
    did_list_tools: bool
    mcp_tools: list[dict]


# ============================================================
# MCP client helpers
# ============================================================
# We prefer langchain-mcp-adapters because it hides MCP protocol details.
# If you don't have it, install:
#   pip install langchain-mcp-adapters mcp
try:
    import asyncio
    from langchain_mcp_adapters.client import StdioMCPClient
    from langchain_mcp_adapters.tools import load_mcp_tools

    _HAS_MCP_ADAPTERS = True
except Exception:
    _HAS_MCP_ADAPTERS = False


def _server_cmd_list() -> list[str]:
    cmd = [MCP_SERVER_CMD]
    if MCP_SERVER_ARGS:
        cmd += MCP_SERVER_ARGS.split()
    return cmd


class _MCPBridge:
    """
    A small bridge that can:
    - list MCP tools (cached)
    - call any MCP tool by name
    Uses langchain-mcp-adapters if available.
    """

    def __init__(self) -> None:
        self._tools_cache: Optional[List[dict]] = None
        self._tool_objs_cache: Optional[list] = None  # LangChain tool objects loaded from MCP
        self._mcp_client: Optional[Any] = None

    def _ensure_ready(self) -> None:
        if not _HAS_MCP_ADAPTERS:
            raise RuntimeError(
                "Missing MCP adapters. Install with: pip install langchain-mcp-adapters mcp"
            )

        if self._mcp_client is None:
            self._mcp_client = StdioMCPClient(command=_server_cmd_list())

    def load_tools_sync(self) -> list:
        """
        Load MCP tools as LangChain tools (once).
        This is helpful if you want to pass them directly to create_agent,
        but here we mainly use list+generic-call tools to keep it stable.
        """
        self._ensure_ready()
        if self._tool_objs_cache is not None:
            return self._tool_objs_cache

        async def _load():
            return await load_mcp_tools(self._mcp_client)

        self._tool_objs_cache = asyncio.run(_load())
        return self._tool_objs_cache

    def list_tools_sync(self) -> List[dict]:
        """
        Best-effort: infer tool schemas from loaded tool objects.
        """
        self._ensure_ready()
        if self._tools_cache is not None:
            return self._tools_cache

        tools = self.load_tools_sync()
        out = []
        for t in tools:
            # t is a LangChain tool object wrapping MCP tool
            out.append(
                {
                    "name": getattr(t, "name", ""),
                    "description": getattr(t, "description", ""),
                    # args_schema is often Pydantic; we keep it lightweight
                    "args_schema": str(getattr(t, "args_schema", "")) if getattr(t, "args_schema", None) else None,
                }
            )

        self._tools_cache = out
        return out

    def call_tool_sync(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
        Call an MCP tool by name through loaded tool objects.
        """
        self._ensure_ready()
        tools = self.load_tools_sync()
        tmap = {t.name: t for t in tools if getattr(t, "name", None)}
        if tool_name not in tmap:
            raise ValueError(f"Unknown MCP tool: {tool_name}. Call mcp_list_tools() first.")

        tool_obj = tmap[tool_name]
        # Most LangChain tools accept dict input
        return tool_obj.invoke(args)


_MCP = _MCPBridge()


# ============================================================
# Tools (Agent-facing)
# ============================================================
@tool
def mcp_list_tools(runtime: ToolRuntime[Context]) -> dict:
    """List tools exposed by the MCP FlexRIC server (best-effort)."""
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"type": "custom", "tool": "mcp_list_tools", "stage": "start"})

    tools = _MCP.list_tools_sync()

    # persist into state (best-effort)
    st = getattr(runtime, "state", None)
    if isinstance(st, dict):
        st["did_list_tools"] = True
        st["mcp_tools"] = tools

    if writer:
        writer({"type": "custom", "tool": "mcp_list_tools", "stage": "done", "count": len(tools)})

    return {"count": len(tools), "tools": tools}


@tool
def mcp_call_tool(tool_name: str, args_json: str, runtime: ToolRuntime[Context]) -> dict:
    """
    Call an MCP FlexRIC tool by name.

    args_json: a JSON string of tool arguments, e.g.:
      {"e2_node_id":"gnb1","pos_low":0,"pos_high":50,"some_flag":true}
    """
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"type": "custom", "tool": "mcp_call_tool", "stage": "start", "tool_name": tool_name})

    try:
        args = json.loads(args_json) if args_json else {}
        if not isinstance(args, dict):
            raise ValueError("args_json must decode to a JSON object/dict")
    except Exception as e:
        raise ValueError(f"Invalid args_json: {repr(e)}")

    result = _MCP.call_tool_sync(tool_name=tool_name, args=args)

    if writer:
        writer({"type": "custom", "tool": "mcp_call_tool", "stage": "done", "tool_name": tool_name})

    # Ensure dict output
    if isinstance(result, dict):
        return {"tool": tool_name, "args": args, "result": result}

    return {"tool": tool_name, "args": args, "result": result}


@tool
def session_info(runtime: ToolRuntime[Context]) -> dict:
    """Return basic session info from runtime context + selected state fields."""
    ctx = runtime.context
    state = getattr(runtime, "state", {}) or {}
    return {
        "context": {"user_id": ctx.user_id, "session_id": ctx.session_id},
        "state": {
            "tool_calls": state.get("tool_calls", 0),
            "did_list_tools": state.get("did_list_tools", False),
            "tool_errors": state.get("tool_errors", [])[-5:],
        },
    }


TOOLS = [mcp_list_tools, mcp_call_tool, session_info]


# ============================================================
# ReAct system prompt template
# ============================================================
BASE_SYSTEM_PROMPT = """You are AgentRIC, an assistant for O-RAN near-RT RIC operations via FlexRIC tools exposed through MCP.

You MUST follow the ReAct pattern:

- Reasoning: write ONE short sentence describing what you will do next.
- Acting: call ONE or more tools ONLY when needed.
- Observation: use tool results to decide next steps.
- Repeat until you can answer.
- Final: provide a concise final answer.

Rules:
- If you don't know the available MCP tools, call mcp_list_tools first.
- Never invent E2 node IDs, slice IDs, RNTIs, cell IDs, or PRB values.
- Prefer read/observe tools before control tools.
"""


def _latest_user_text(state: dict) -> str:
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        role = getattr(m, "type", None) or getattr(m, "role", None)
        content = getattr(m, "content", None)
        if role in ("human", "user") and isinstance(content, str):
            return content
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content", "")
    return ""


# ============================================================
# Middleware: dynamic prompt + tool gating + monitoring + error handling
# ============================================================
class AgentRICMiddleware(AgentMiddleware):
    """
    Adds guardrails on top of the non-secured MCP server:
    - allowlist e2_node_id (if configured)
    - simple rate limiting
    - dry_run blocking (best-effort)
    - PRB bounds checks (best-effort)
    - audit logs
    - tool error recovery
    """

    def __init__(self) -> None:
        self._window_start = 0.0
        self._window_calls = 0

    # ---- model call (sync) ----
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        updated = self._prepare_request(request)
        return handler(updated)

    # ---- model call (async) ----
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> ModelResponse:
        updated = self._prepare_request(request)
        return await handler(updated)

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        state = request.state or {}
        runtime = request.runtime

        ctx = getattr(runtime, "context", None)
        ctx_line = ""
        if ctx:
            ctx_line = f"\nContext: user_id={getattr(ctx,'user_id','')}, session_id={getattr(ctx,'session_id','')}\n"

        did_list = bool(state.get("did_list_tools", False))
        user_text = _latest_user_text(state)

        prompt = BASE_SYSTEM_PROMPT + ctx_line

        if not did_list:
            prompt += "\nNote: You have not listed MCP tools yet. Use mcp_list_tools if you need tool names.\n"

        if DRY_RUN:
            prompt += "\nDRY_RUN is enabled: do NOT perform changes; prefer observation/read operations.\n"

        if E2_NODE_ALLOWLIST:
            prompt += f"\nE2 allowlist is enabled: only use e2_node_id in {sorted(E2_NODE_ALLOWLIST)}.\n"

        # Attach prompt (tools unchanged here; tool gating happens on tool call)
        updated = request.override(system_prompt=prompt, tools=request.tools)

        writer = getattr(runtime, "stream_writer", None)
        if writer:
            writer(
                {
                    "type": "custom",
                    "name": "middleware",
                    "stage": "prepared",
                    "did_list_tools": did_list,
                    "dry_run": DRY_RUN,
                }
            )

        return updated

    # ---- tool call (sync) ----
    def wrap_tool_call(self, request, handler):
        return self._run_tool_with_monitoring(request, handler, is_async=False)

    # ---- tool call (async) ----
    async def awrap_tool_call(self, request, handler):
        return await self._run_tool_with_monitoring(request, handler, is_async=True)

    def _audit(self, runtime: ToolRuntime[Context], record: dict) -> None:
        # keep in state
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st.setdefault("audit", [])
            if isinstance(st["audit"], list):
                st["audit"].append(record)

        # append to file
        try:
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # do not crash agent on audit failures
            pass

    def _rate_limit_ok(self) -> bool:
        now = time.time()
        # reset window every 60s
        if now - self._window_start >= 60:
            self._window_start = now
            self._window_calls = 0

        self._window_calls += 1

        # burst protection: early high calls rejected
        if self._window_calls > RATE_LIMIT_MAX_CALLS_PER_MIN:
            return False
        if self._window_calls > RATE_LIMIT_BURST and (now - self._window_start) < 5:
            return False
        return True

    def _extract_mcp_call(self, request) -> Optional[tuple[str, dict]]:
        """
        If the tool is mcp_call_tool, extract tool_name + args dict (best-effort).
        """
        name = request.tool_call.get("name")
        if name != "mcp_call_tool":
            return None

        args = request.tool_call.get("args", {}) or {}
        tool_name = args.get("tool_name")
        args_json = args.get("args_json", "")

        if not isinstance(tool_name, str):
            return None

        try:
            decoded = json.loads(args_json) if args_json else {}
            if not isinstance(decoded, dict):
                decoded = {}
        except Exception:
            decoded = {}

        return tool_name, decoded

    def _guard_mcp(self, tool_name: str, tool_args: dict) -> Optional[str]:
        """
        Return error string if blocked, else None.
        """
        # Allowlist enforcement (only if args include e2_node_id)
        if E2_NODE_ALLOWLIST:
            e2 = tool_args.get("e2_node_id") or tool_args.get("e2NodeId") or tool_args.get("node_id")
            if isinstance(e2, str) and e2 and e2 not in E2_NODE_ALLOWLIST:
                return f"Blocked: e2_node_id '{e2}' not in allowlist."

        # PRB bounds best-effort
        pos_low = tool_args.get("pos_low")
        pos_high = tool_args.get("pos_high")
        if isinstance(pos_low, int) and isinstance(pos_high, int):
            if pos_low < PRB_POS_LOW or pos_high > PRB_POS_HIGH or pos_low > pos_high:
                return f"Blocked: PRB range invalid (pos_low={pos_low}, pos_high={pos_high}) with bounds [{PRB_POS_LOW},{PRB_POS_HIGH}]."

        # Dry run best-effort: block likely-control tools by name patterns
        if DRY_RUN:
            lowered = tool_name.lower()
            controlish = any(k in lowered for k in ["control", "set", "write", "policy", "handover", "allocate", "prb"])
            if controlish:
                return f"Blocked by DRY_RUN: refusing to call control-like tool '{tool_name}'."

        return None

    def _run_tool_with_monitoring(self, request, handler, is_async: bool):
        runtime = request.runtime
        tool_name = request.tool_call.get("name")
        tool_args = request.tool_call.get("args", {})

        writer = getattr(runtime, "stream_writer", None)

        # bump tool_calls counter
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st["tool_calls"] = int(st.get("tool_calls", 0)) + 1
            st.setdefault("last_tools", [])
            if isinstance(st["last_tools"], list):
                st["last_tools"].append({"name": tool_name, "args": tool_args, "ts": time.time()})

        # rate limit
        if not self._rate_limit_ok():
            msg = "Rate limit exceeded in agent middleware."
            self._append_error(runtime, f"{tool_name}: {msg}")
            return ToolMessage(
                content=json.dumps({"status": "error", "tool": tool_name, "message": msg}),
                tool_call_id=request.tool_call.get("id", ""),
            )

        # MCP-specific guards (only for mcp_call_tool)
        extracted = self._extract_mcp_call(request)
        if extracted:
            inner_tool_name, inner_args = extracted
            blocked_reason = self._guard_mcp(inner_tool_name, inner_args)
            if blocked_reason:
                self._audit(
                    runtime,
                    {
                        "ts": time.time(),
                        "kind": "blocked",
                        "outer_tool": "mcp_call_tool",
                        "inner_tool": inner_tool_name,
                        "inner_args": inner_args,
                        "reason": blocked_reason,
                    },
                )
                self._append_error(runtime, f"{inner_tool_name}: {blocked_reason}")
                return ToolMessage(
                    content=json.dumps(
                        {"status": "blocked", "tool": inner_tool_name, "message": blocked_reason}
                    ),
                    tool_call_id=request.tool_call.get("id", ""),
                )

        # audit start
        self._audit(
            runtime,
            {"ts": time.time(), "kind": "call", "tool": tool_name, "args": tool_args},
        )

        if writer:
            writer({"type": "custom", "name": "tool_monitor", "stage": "start", "tool": tool_name})

        try:
            if is_async:
                async def _awaited():
                    return await handler(request)
                return _awaited()
            else:
                return handler(request)

        except Exception as e:
            err = repr(e)
            self._append_error(runtime, f"{tool_name}: {err}")

            if writer:
                writer({"type": "custom", "name": "tool_monitor", "stage": "error", "tool": tool_name, "error": err})

            self._audit(
                runtime,
                {"ts": time.time(), "kind": "error", "tool": tool_name, "args": tool_args, "error": err},
            )

            return ToolMessage(
                content=json.dumps({"status": "error", "tool": tool_name, "message": str(e)}),
                tool_call_id=request.tool_call.get("id", ""),
            )

    def _append_error(self, runtime: ToolRuntime[Context], msg: str) -> None:
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st.setdefault("tool_errors", [])
            if isinstance(st["tool_errors"], list):
                st["tool_errors"].append(msg)


# ============================================================
# State update middleware (persist list-tools output)
# ============================================================
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
        payload: Any = None
        if isinstance(result, ToolMessage):
            # ToolMessage content is string
            try:
                payload = json.loads(result.content)
            except Exception:
                payload = result.content
        else:
            payload = getattr(result, "content", None) or result

        if name == "mcp_list_tools" and isinstance(payload, dict):
            # We already set in tool, but double-ensure
            st["did_list_tools"] = True
            if "tools" in payload and isinstance(payload["tools"], list):
                st["mcp_tools"] = payload["tools"]


# ============================================================
# Model + Agent graph
# ============================================================
model = ChatOllama(
    model=MODEL,
    base_url=OLLAMA_URL,
    temperature=0,
)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
    middleware=[
        AgentRICMiddleware(),
        PersistToolResultsMiddleware(),
    ],
)

# LangGraph dev server discovers this variable by graph_id
__all__ = ["agent", "Context", "CustomState"]
