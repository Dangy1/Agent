import os
import base64
import json
import requests
from dataclasses import dataclass
from dotenv import load_dotenv
from typing import List, Dict, Any, Callable

# IMPORTANT for Python < 3.12 + Pydantic v2 ToolStrategy:
from typing_extensions import TypedDict, NotRequired

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.agents.middleware import wrap_model_call, wrap_tool_call, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest

# ✅ Use one consistent messages module for streaming + ToolMessage
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage, HumanMessage


# =========================
# 0) Env + connection check (11434 preferred)
# =========================
load_dotenv()

MODEL = "gpt-oss:latest"
HOST = "130.233.158.22"
URL_11434 = f"http://{HOST}:11434"
URL_8080  = f"http://{HOST}:8080"

user = os.getenv("OLLAMA_USER", "")
pwd  = os.getenv("OLLAMA_PASS", "")

def basic_auth_headers(u: str, p: str) -> dict:
    basic = base64.b64encode(f"{u}:{p}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {basic}"}

def can_get_tags(base_url: str, headers: dict | None = None) -> bool:
    try:
        r = requests.get(f"{base_url}/api/tags", headers=headers, timeout=5)
        return r.status_code == 200
    except Exception:
        return False

def can_post_chat(base_url: str, model: str, headers: dict | None = None) -> bool:
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "stream": False}
    try:
        r = requests.post(f"{base_url}/api/chat", headers=headers, json=payload, timeout=15)
        return r.status_code == 200
    except Exception:
        return False

if can_get_tags(URL_11434) and can_post_chat(URL_11434, MODEL):
    OLLAMA_URL = URL_11434
    client_kwargs: dict = {}
    print(f"[Using] {OLLAMA_URL} (direct 11434, no auth)")
else:
    if not user or not pwd:
        raise RuntimeError("11434 not usable and missing OLLAMA_USER/OLLAMA_PASS for 8080 fallback.")

    headers = basic_auth_headers(user, pwd)
    if not (can_get_tags(URL_8080, headers=headers) and can_post_chat(URL_8080, MODEL, headers=headers)):
        raise RuntimeError("8080 not usable with provided Basic Auth.")

    OLLAMA_URL = URL_8080
    client_kwargs = {"headers": headers}
    print(f"[Using] {OLLAMA_URL} (8080 with Basic Auth)")


# =========================
# 1) Runtime context schema
# =========================
@dataclass
class Context:
    user_id: str
    locale: str = "en"
    persona: str = "shopping-assistant"


# =========================
# 2) Custom short-term memory (custom Agent state)
# =========================
class CustomState(TypedDict):
    last_products: NotRequired[List[Dict[str, Any]]]
    stock_map: NotRequired[Dict[str, Dict[str, Any]]]
    authenticated: NotRequired[bool]


# =========================
# 3) Tools (stubs; replace with real APIs)
# =========================
@tool
def search_products(query: str, runtime: ToolRuntime[Context]) -> List[Dict[str, str]]:
    """Search popular products (stub)."""
    _ = runtime.context.user_id
    q = query.lower()
    if "wireless" in q and "headphone" in q:
        return [
            {"name": "Sony WH-1000XM5", "retailer": "Amazon"},
            {"name": "Bose QuietComfort Ultra Headphones", "retailer": "Best Buy"},
            {"name": "Apple AirPods Pro (2nd gen)", "retailer": "Apple"},
            {"name": "Sennheiser Momentum 4 Wireless", "retailer": "Amazon"},
        ]
    return [{"name": "Unknown", "retailer": "Unknown"}]

@tool
def check_stock(product_name: str, retailer: str) -> Dict[str, Any]:
    """Check stock (stub)."""
    r = retailer.lower()
    if "amazon" in r or "apple" in r:
        return {"product_name": product_name, "in_stock": True, "note": f"{retailer}: In stock (demo)"}
    if "best buy" in r:
        return {"product_name": product_name, "in_stock": False, "note": f"{retailer}: Out of stock / limited (demo)"}
    return {"product_name": product_name, "in_stock": False, "note": f"{retailer}: Unknown availability (demo)"}

ALL_TOOLS = [search_products, check_stock]


# =========================
# 4) ToolStrategy structured output schema
# =========================
class ResponseFormat(TypedDict):
    top_products: List[Dict[str, str]]
    stock_status: Dict[str, Dict[str, Any]]
    summary: str


# =========================
# 5) Helpers
# =========================
def _state_as_custom(state: dict) -> CustomState:
    return state  # type: ignore

def _latest_user_text(state: dict) -> str:
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content", "") or ""
        role = getattr(m, "role", None) or getattr(m, "type", None)
        content = getattr(m, "content", None)
        if role in ("user", "human") and isinstance(content, str):
            return content
    return ""


# =========================
# 6) Middleware: memory sync + dynamic prompt + tool gating + tool errors
# =========================
@wrap_model_call
def sync_memory_from_tool_observations(request: ModelRequest, handler) -> ModelResponse:
    st = _state_as_custom(request.state)
    msgs = request.state.get("messages", [])

    last_products = st.get("last_products")
    stock_map = st.get("stock_map", {})

    for m in msgs[-30:]:
        if getattr(m, "type", None) == "tool":
            content = getattr(m, "content", "")
            if not isinstance(content, str):
                continue

            # search_products output (list[dict])
            if content.strip().startswith("[") and "retailer" in content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        last_products = parsed
                except Exception:
                    pass

            # check_stock output (dict with product_name)
            if content.strip().startswith("{") and "product_name" in content:
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "product_name" in parsed:
                        stock_map[parsed["product_name"]] = parsed
                except Exception:
                    pass

    if last_products is not None:
        st["last_products"] = last_products
    if stock_map:
        st["stock_map"] = stock_map

    return handler(request.override(state=st))


@wrap_model_call
def dynamic_system_prompt(request: ModelRequest, handler) -> ModelResponse:
    ctx = getattr(request.runtime, "context", None)
    user_id = getattr(ctx, "user_id", "unknown")
    locale = getattr(ctx, "locale", "en")
    persona = getattr(ctx, "persona", "shopping-assistant")

    st = _state_as_custom(request.state)
    have_products = bool(st.get("last_products"))
    have_stock = bool(st.get("stock_map"))
    turns = len(request.state.get("messages", []))

    sys = f"""You are a ReAct shopping agent ({persona}).
Locale={locale}, user_id={user_id}, turns={turns}

Goal: Identify the most popular wireless headphones right now and verify availability.

Tools:
- search_products(query)
- check_stock(product_name, retailer)

Memory:
- last_products: cached candidates
- stock_map: cached stock results

Policy:
- If last_products missing OR user asks for "latest/right now", call search_products("wireless headphones").
- Otherwise reuse last_products.
- If stock_map missing/incomplete, call check_stock for top candidates.
- Use observations before answering.

Return ONLY valid JSON matching:
{{
  "top_products": [{{"name": "...", "retailer": "..."}}],
  "stock_status": {{"Product Name": {{"in_stock": true, "note": "..."}}}},
  "summary": "..."
}}
StateHint: have_products={have_products}, have_stock={have_stock}
"""
    return handler(request.override(system_message=SystemMessage(content=sys)))


@wrap_model_call
def gate_tools_by_state(request: ModelRequest, handler) -> ModelResponse:
    st = _state_as_custom(request.state)
    text = _latest_user_text(request.state).lower()
    wants_latest = any(k in text for k in ["latest", "right now", "most popular", "today"])
    have_products = bool(st.get("last_products"))

    if not have_products or wants_latest:
        gated = [t for t in request.tools if t.name == "search_products"]
        print("[ToolGate] enabling:", [t.name for t in gated])
        return handler(request.override(tools=gated))

    print("[ToolGate] enabling:", [t.name for t in request.tools])
    return handler(request)


@wrap_tool_call
def handle_tool_errors(request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage]) -> ToolMessage:
    try:
        return handler(request)
    except Exception as e:
        tool_call_id = request.tool_call.get("id", "")
        name = request.tool_call.get("name", "unknown_tool")
        msg = f"Tool error in {name}: {str(e)}"
        print("[ToolError]", msg)
        return ToolMessage(content=msg, tool_call_id=tool_call_id)


# =========================
# 7) Model + agent
# =========================
model = ChatOllama(
    model=MODEL,
    base_url=OLLAMA_URL,
    temperature=0,
    client_kwargs=client_kwargs,
)

checkpointer = InMemorySaver()

agent = create_agent(
    model=model,
    tools=ALL_TOOLS,
    middleware=[
        sync_memory_from_tool_observations,
        dynamic_system_prompt,
        gate_tools_by_state,
        handle_tool_errors,
    ],
    checkpointer=checkpointer,
    context_schema=Context,
    response_format=ToolStrategy(ResponseFormat),
)


# =========================
# 8) Streaming demo
# =========================
def print_stream(chunks):
    """
    stream_mode="values" yields the full state each time.
    We'll print only NEW messages since the last chunk.
    """
    last_len = 0
    for chunk in chunks:
        msgs = chunk.get("messages", [])
        if not msgs:
            continue

        new_msgs = msgs[last_len:]
        last_len = len(msgs)

        for m in new_msgs:
            if isinstance(m, HumanMessage) and m.content:
                print(f"User: {m.content}")

            elif isinstance(m, AIMessage):
                tool_calls = getattr(m, "tool_calls", None) or []
                if tool_calls:
                    print(f"Calling tools: {[tc.get('name') for tc in tool_calls]}")
                elif m.content:
                    print(f"Agent: {m.content}")

            elif isinstance(m, ToolMessage) and m.content:
                print(f"Observation: {m.content}")


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "headphones-demo-stream"}}
    ctx = Context(user_id="1", locale="en", persona="shopping-assistant")

    query = "Find the most popular wireless headphones right now and check if they're in stock"

    chunks = agent.stream(
        {"messages": [{"role": "user", "content": query}]},
        config=config,
        context=ctx,
        stream_mode="values",
    )

    print_stream(chunks)
