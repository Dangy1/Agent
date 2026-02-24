import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import ToolMessage

# IMPORTANT: DO NOT provide custom checkpointer/store in LangGraph API dev runtime
# (persistence is handled automatically by the runtime)

load_dotenv()

# -------------------------
# Ollama endpoint selection
# -------------------------
HOST = os.getenv("OLLAMA_HOST", "130.233.158.22")
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{HOST}:11434")
MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:latest")


# -------------------------
# Runtime Context (immutable)
# -------------------------
@dataclass(frozen=True)
class Context:
    user_id: str
    session_id: str


# -------------------------
# Custom short-term memory (state_schema)
# -------------------------
# ✅ Avoid metaclass conflict:
# - Do NOT combine AgentState with typing.TypedDict explicitly.
# - Just subclass AgentState directly (LangChain will treat it as a TypedDict-like schema).
class CustomState(AgentState):
    # the default AgentState already includes: messages: list[BaseMessage]
    did_search: bool
    searched_query: str
    search_results: list[dict]
    stock_results: list[dict]
    tool_errors: list[str]
    tool_calls: int


# -------------------------
# Tools
# -------------------------
@tool
def search_products(query: str, runtime: ToolRuntime[Context]) -> dict:
    """Search for products by query. Returns top 'popular' candidates (demo stub)."""
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer({"type": "custom", "tool": "search_products", "stage": "start", "query": query})

    # Demo stub (replace with real web/store search)
    results = [
        {"name": "Sony WH-1000XM5", "where": "Amazon"},
        {"name": "Bose QuietComfort Ultra Headphones", "where": "Best Buy"},
        {"name": "Apple AirPods Pro (2nd gen)", "where": "Apple Store"},
    ]

    # Update short-term memory (state) using Command-style update is often used in LangGraph nodes,
    # but in create_agent tools, the simplest is: return payload and let middleware update state.
    # We'll return results; middleware will persist into state.
    if writer:
        writer({"type": "custom", "tool": "search_products", "stage": "done", "count": len(results)})

    return {"query": query, "results": results}


@tool
def check_stock(product: str, where: str, runtime: ToolRuntime[Context]) -> dict:
    """Check stock for a product at a retailer (demo stub)."""
    writer = getattr(runtime, "stream_writer", None)
    if writer:
        writer(
            {
                "type": "custom",
                "tool": "check_stock",
                "stage": "checking",
                "product": product,
                "where": where,
            }
        )

    # Demo stock logic (replace with real retailer API checks)
    in_stock = True
    if "Bose" in product and "Best Buy" in where:
        in_stock = False

    if writer:
        writer(
            {
                "type": "custom",
                "tool": "check_stock",
                "stage": "done",
                "product": product,
                "where": where,
                "in_stock": in_stock,
            }
        )

    return {"product": product, "where": where, "in_stock": in_stock}


@tool
def session_info(runtime: ToolRuntime[Context]) -> dict:
    """Return basic session info from runtime context + selected state fields."""
    ctx = runtime.context
    state = getattr(runtime, "state", {}) or {}
    return {
        "context": {"user_id": ctx.user_id, "session_id": ctx.session_id},
        "state": {
            "did_search": state.get("did_search", False),
            "tool_calls": state.get("tool_calls", 0),
        },
    }


TOOLS = [search_products, check_stock, session_info]


# -------------------------
# ReAct system prompt template
# -------------------------
BASE_SYSTEM_PROMPT = """You are a shopping assistant.

You MUST follow the ReAct pattern:

- Reasoning: write ONE short sentence describing what you will do next.
- Acting: call ONE or more tools ONLY when needed.
- Observation: use tool results to decide next steps.
- Repeat until you can answer.
- Final: provide a concise final answer.

Task:
- Identify the current most popular wireless headphones and verify availability (in stock).

Rules:
- Popularity is time-sensitive: ALWAYS call search_products("wireless headphones") first.
- After search, verify stock for the top results using check_stock(product, where).
- If a tool fails, recover gracefully and continue when possible.
"""


def _latest_user_text(state: dict) -> str:
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        # LangChain messages
        role = getattr(m, "type", None) or getattr(m, "role", None)
        content = getattr(m, "content", None)
        if role in ("human", "user") and isinstance(content, str):
            return content
        # dict messages
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content", "")
    return ""


# -------------------------
# Middleware: dynamic prompt + tool gating + monitoring + error handling
# -------------------------
class DynamicReActMiddleware(AgentMiddleware):
    """
    - Dynamically updates system prompt based on state + context
    - Dynamically enables/disables tools based on state
    - Logs tool usage
    - Handles tool errors (sync + async)
    """

    # ---- model call (sync) ----
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        updated = self._prepare_request(request)
        return handler(updated)

    # ---- model call (async) ----
    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Any]
    ) -> ModelResponse:
        updated = self._prepare_request(request)
        return await handler(updated)

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        state = request.state or {}
        runtime = request.runtime

        did_search = bool(state.get("did_search", False))
        tool_calls = int(state.get("tool_calls", 0))
        user_text = _latest_user_text(state)

        # Dynamic system prompt (inject runtime context)
        ctx = getattr(runtime, "context", None)
        ctx_line = ""
        if ctx:
            ctx_line = f"\nContext: user_id={getattr(ctx,'user_id', '')}, session_id={getattr(ctx,'session_id','')}\n"

        prompt = BASE_SYSTEM_PROMPT + ctx_line

        # Add a small nudge if user didn't ask clearly
        if "headphone" not in user_text.lower():
            prompt += "\nNote: If the user is not asking about headphones, ask a clarifying question.\n"

        # Tool gating:
        # - Always allow search_products
        # - Only allow check_stock after did_search == True
        allowed = []
        for t in request.tools:
            if t.name == "search_products":
                allowed.append(t)
            elif t.name == "check_stock" and did_search:
                allowed.append(t)
            elif t.name == "session_info":
                allowed.append(t)

        # Attach prompt + filtered tools
        # create_agent uses system_prompt kwarg; middleware overrides request.system_message internally.
        # The supported override is request.override(system_prompt=..., tools=...)
        updated = request.override(system_prompt=prompt, tools=allowed)

        # Optional: emit custom progress
        writer = getattr(runtime, "stream_writer", None)
        if writer:
            writer(
                {
                    "type": "custom",
                    "name": "middleware",
                    "stage": "prepared",
                    "did_search": did_search,
                    "tool_calls": tool_calls,
                    "enabled_tools": [t.name for t in allowed],
                }
            )

        return updated

    # ---- tool call (sync) ----
    def wrap_tool_call(self, request, handler):
        return self._run_tool_with_monitoring(request, handler, is_async=False)

    # ---- tool call (async) ----
    async def awrap_tool_call(self, request, handler):
        return await self._run_tool_with_monitoring(request, handler, is_async=True)

    def _state_bump(self, runtime: ToolRuntime[Context], **updates: Any) -> None:
        # Best-effort state update. Some runtimes expose runtime.state as mutable dict.
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st.update(updates)

    def _append_state_list(self, runtime: ToolRuntime[Context], key: str, item: Any) -> None:
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st.setdefault(key, [])
            if isinstance(st[key], list):
                st[key].append(item)

    def _run_tool_with_monitoring(self, request, handler, is_async: bool):
        runtime = request.runtime
        tool_name = request.tool_call.get("name")
        tool_args = request.tool_call.get("args", {})

        writer = getattr(runtime, "stream_writer", None)
        if writer:
            writer({"type": "custom", "name": "tool_monitor", "stage": "start", "tool": tool_name, "args": tool_args})

        # bump tool_calls counter
        st = getattr(runtime, "state", None)
        if isinstance(st, dict):
            st["tool_calls"] = int(st.get("tool_calls", 0)) + 1

        try:
            if is_async:
                # handler returns a ToolMessage/Command-like result
                async def _awaited():
                    return await handler(request)
                return _awaited()
            else:
                return handler(request)

        except Exception as e:
            # Store error in state
            self._append_state_list(runtime, "tool_errors", f"{tool_name}: {repr(e)}")

            if writer:
                writer({"type": "custom", "name": "tool_monitor", "stage": "error", "tool": tool_name, "error": repr(e)})

            # Return ToolMessage so the model can continue the loop gracefully
            return ToolMessage(
                content=f'{{"status":"error","tool":"{tool_name}","message":{repr(str(e))}}}',
                tool_call_id=request.tool_call.get("id", ""),
            )


# -------------------------
# State update middleware (persist search/stock results into state)
# -------------------------
class PersistToolResultsMiddleware(AgentMiddleware):
    """Persist selected tool outputs into short-term memory state."""

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

        # ToolMessage content is usually a string; for StructuredTool result it may be dict already.
        payload: Any = None
        if isinstance(result, ToolMessage):
            payload = result.content
        else:
            payload = getattr(result, "content", None) or result

        # If tool returns dict, store directly
        if name == "search_products" and isinstance(payload, dict):
            st["did_search"] = True
            st["searched_query"] = payload.get("query", "")
            st["search_results"] = payload.get("results", [])
        elif name == "check_stock" and isinstance(payload, dict):
            st.setdefault("stock_results", [])
            st["stock_results"].append(payload)


# -------------------------
# Model + Agent graph
# -------------------------
model = ChatOllama(
    model=MODEL,
    base_url=OLLAMA_URL,
    temperature=0,
)

agent = create_agent(
    model=model,
    tools=TOOLS,                      # pre-registered tools
    system_prompt=BASE_SYSTEM_PROMPT, # base prompt (middleware overrides dynamically)
    state_schema=CustomState,         # short-term memory fields
    middleware=[
        DynamicReActMiddleware(),
        PersistToolResultsMiddleware(),
    ],
)

# LangGraph dev server discovers this variable by graph_id
# (file=agent.py, variable=agent)
__all__ = ["agent", "Context", "CustomState"]
