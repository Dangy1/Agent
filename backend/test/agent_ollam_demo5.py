import os
import base64
import requests
from dataclasses import dataclass
from dotenv import load_dotenv
from typing import Callable, List

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langchain.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.agents.middleware import wrap_model_call, wrap_tool_call, ModelRequest
from langchain.tools.tool_node import ToolCallRequest


# =========================
# 0) Env + connection check (11434 preferred)
# =========================
load_dotenv()

MODEL = "gpt-oss:latest"
HOST = "130.233.158.22"
URL_11434 = f"http://{HOST}:11434"
URL_8080 = f"http://{HOST}:8080"

user = os.getenv("OLLAMA_USER", "")
pwd = os.getenv("OLLAMA_PASS", "")

def basic_auth_headers(u: str, p: str) -> dict:
    basic = base64.b64encode(f"{u}:{p}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {basic}"}

def can_get_tags(base_url: str, headers: dict | None = None) -> tuple[bool, str]:
    try:
        r = requests.get(f"{base_url}/api/tags", headers=headers, timeout=5)
        if r.status_code == 200:
            return True, "OK"
        return False, f"GET /api/tags -> {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"GET /api/tags exception: {repr(e)}"

def can_post_chat(base_url: str, model: str, headers: dict | None = None) -> tuple[bool, str]:
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "stream": False}
    try:
        r = requests.post(f"{base_url}/api/chat", headers=headers, json=payload, timeout=15)
        if r.status_code == 200:
            return True, "OK"
        return False, f"POST /api/chat -> {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"POST /api/chat exception: {repr(e)}"

ok_tags, _ = can_get_tags(URL_11434)
ok_chat, _ = can_post_chat(URL_11434, MODEL)

if ok_tags and ok_chat:
    OLLAMA_URL = URL_11434
    client_kwargs = {}
    print(f"[Using] {OLLAMA_URL} (direct 11434, no auth)")
else:
    if not user or not pwd:
        raise RuntimeError("11434 not usable, and missing OLLAMA_USER/OLLAMA_PASS for 8080 fallback.")

    headers = basic_auth_headers(user, pwd)
    ok_tags2, d2 = can_get_tags(URL_8080, headers=headers)
    ok_chat2, c2 = can_post_chat(URL_8080, MODEL, headers=headers)

    if not (ok_tags2 and ok_chat2):
        raise RuntimeError(f"8080 not usable: tags={d2} chat={c2}")

    OLLAMA_URL = URL_8080
    client_kwargs = {"headers": headers}
    print(f"[Using] {OLLAMA_URL} (8080 with Basic Auth)")


# =========================
# 1) Runtime context schema
# =========================
@dataclass
class Context:
    user_id: str


# =========================
# 2) Static tools (pre-registered)
# =========================
@tool
def get_user_location(runtime: ToolRuntime[Context]) -> str:
    """Return the user's location based on runtime context."""
    uid = runtime.context.user_id
    return "Florida" if uid == "1" else "San Francisco"

@tool
def get_weather_for_location(city: str) -> str:
    """Get weather for a given city (stub)."""
    if city.lower() in ("florida", "miami"):
        return "Sunny, 27°C, light breeze."
    if city.lower() in ("sf", "san francisco"):
        return "Foggy, 14°C, cool wind."
    return f"Partly cloudy in {city}."

@tool
def session_info(runtime: ToolRuntime[Context]) -> str:
    """Show runtime context + any state keys (if exposed)."""
    uid = runtime.context.user_id
    state = getattr(runtime, "state", None)
    keys = list(state.keys()) if isinstance(state, dict) else []
    return f"user_id={uid}, state_keys={keys}"

STATIC_TOOLS = [get_user_location, get_weather_for_location, session_info]


# =========================
# 3) Dynamic tool (added only when relevant)
# =========================
@tool
def calculate_tip(bill_amount: float, tip_percentage: float = 20.0) -> str:
    """Calculate tip for a bill."""
    tip = bill_amount * (tip_percentage / 100.0)
    total = bill_amount + tip
    return f"Tip: ${tip:.2f}, Total: ${total:.2f}"


# =========================
# 4) System prompt + response format
# =========================
SYSTEM_PROMPT = """You are an expert assistant who speaks in puns.

Tools:
- get_user_location: user location
- get_weather_for_location: weather for a city
- session_info: runtime context info
- calculate_tip: tip calculation (only if the user asks about bill/tip/total)

Rules:
1) If user asks weather without a city, call get_user_location first.
2) If user is not asking weather, do not call weather tools.
3) Use calculate_tip only when user asks tip/bill/total.
Return your final answer in the required schema.
"""

@dataclass
class ResponseFormat:
    punny_response: str
    weather_conditions: str | None = None


def print_structured_or_fallback(resp: dict, label: str = "") -> None:
    if "structured_response" in resp and resp["structured_response"] is not None:
        print(f"\n[{label}] structured_response:\n{resp['structured_response']}")
        return
    print(f"\n[{label}] No structured_response. Keys={list(resp.keys())}")
    msgs = resp.get("messages", [])
    if msgs:
        last = msgs[-1]
        print("[last message]:", getattr(last, "content", last))
    else:
        print("[raw]:", resp)


# =========================
# 5) Middleware: dynamic tool injection + tool error handling
# =========================
def _latest_user_text(state: dict) -> str:
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            return (m.get("content") or "")
        role = getattr(m, "role", None) or getattr(m, "type", None)
        content = getattr(m, "content", None)
        if role in ("user", "human") and isinstance(content, str):
            return content
    return ""

@wrap_model_call
def add_dynamic_tools(request: ModelRequest, handler) -> "ModelResponse":
    """
    Add calculate_tip dynamically only when the user's message suggests it.
    All tools are still safe because they are in-process and known.
    """
    text = _latest_user_text(request.state).lower()
    wants_tip = any(k in text for k in ["tip", "bill", "total", "%", "gratuity"])

    if wants_tip:
        # Add calculate_tip if not already present
        existing = {t.name for t in request.tools}
        if calculate_tip.name not in existing:
            updated_tools = [*request.tools, calculate_tip]
            print("[DynamicTools] calculate_tip enabled")
            return handler(request.override(tools=updated_tools))

    return handler(request)

@wrap_tool_call
def handle_tool_errors(request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage]) -> ToolMessage:
    """
    Convert tool exceptions into ToolMessage so the agent can recover.
    """
    try:
        return handler(request)
    except Exception as e:
        tool_call_id = request.tool_call.get("id", "")
        name = request.tool_call.get("name", "unknown_tool")
        msg = f"Tool error in {name}: {str(e)}"
        print("[ToolError]", msg)
        return ToolMessage(content=msg, tool_call_id=tool_call_id)


# =========================
# 6) Model + agent + state store
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
    system_prompt=SYSTEM_PROMPT,
    tools=STATIC_TOOLS,                    # pre-registered static tools
    context_schema=Context,                # runtime context schema for ToolRuntime[Context]
    response_format=ToolStrategy(ResponseFormat),
    checkpointer=checkpointer,             # state store per thread_id
    middleware=[add_dynamic_tools, handle_tool_errors],  # dynamic tools + error handling
)


# =========================
# 7) Run demo
# =========================
config = {"configurable": {"thread_id": "1"}}
ctx = Context(user_id="1")

resp1 = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
    config=config,
    context=ctx,
)
print_structured_or_fallback(resp1, label="turn1")

resp2 = agent.invoke(
    {"messages": [{"role": "user", "content": "Calculate a 20% tip on $85"}]},
    config=config,
    context=ctx,
)
print_structured_or_fallback(resp2, label="turn2")

resp3 = agent.invoke(
    {"messages": [{"role": "user", "content": "what do you know about this session?"}]},
    config=config,
    context=ctx,
)
print_structured_or_fallback(resp3, label="turn3")
