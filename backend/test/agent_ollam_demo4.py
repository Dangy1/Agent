import os
import base64
import json
import requests
from dataclasses import dataclass
from typing import Callable, List

from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langchain.messages import SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse
from langchain.agents.middleware import wrap_tool_call
from langchain.tools.tool_node import ToolCallRequest


# =========================
# 0) Load env
# =========================
load_dotenv()

MODEL = "gpt-oss:latest"
HOST = "130.233.158.22"
URL_11434 = f"http://{HOST}:11434"
URL_8080 = f"http://{HOST}:8080"

user = os.getenv("OLLAMA_USER", "")
pwd = os.getenv("OLLAMA_PASS", "")


# =========================
# 1) Keep your checking logic
# =========================
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
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
    try:
        r = requests.post(f"{base_url}/api/chat", headers=headers, json=payload, timeout=15)
        if r.status_code == 200:
            return True, "OK"
        return False, f"POST /api/chat -> {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"POST /api/chat exception: {repr(e)}"

ok_tags, detail = can_get_tags(URL_11434, headers=None)
ok_chat, detail_chat = can_post_chat(URL_11434, MODEL, headers=None)

if ok_tags and ok_chat:
    OLLAMA_URL = URL_11434
    client_kwargs = {}
    print(f"[Using] {OLLAMA_URL} (direct 11434, no auth)")
else:
    print(f"[11434 not usable] tags={detail} chat={detail_chat}")

    if not user or not pwd:
        raise RuntimeError("Missing OLLAMA_USER / OLLAMA_PASS for 8080 fallback")

    headers = basic_auth_headers(user, pwd)

    ok_tags2, detail2 = can_get_tags(URL_8080, headers=headers)
    ok_chat2, detail_chat2 = can_post_chat(URL_8080, MODEL, headers=headers)

    if not (ok_tags2 and ok_chat2):
        raise RuntimeError(f"8080 not usable: tags={detail2} chat={detail_chat2}")

    OLLAMA_URL = URL_8080
    client_kwargs = {"headers": headers}
    print(f"[Using] {OLLAMA_URL} (8080 with Basic Auth)")


# =========================
# 2) Runtime context schema
# =========================
@dataclass
class Context:
    user_id: str


# =========================
# 3) Tools (ALL are pre-registered)
# =========================
@tool
def public_search(query: str) -> str:
    """Public search tool (safe)."""
    return f"[public_search] results for: {query}"

@tool
def private_search(query: str, runtime: ToolRuntime[Context]) -> str:
    """Private search tool (requires 'authenticated' state hint)."""
    return f"[private_search] user={runtime.context.user_id}, query={query}"

@tool
def advanced_search(query: str) -> str:
    """Advanced search tool (expensive)."""
    return f"[advanced_search] deep results for: {query}"

@tool
def get_user_location(runtime: ToolRuntime[Context]) -> str:
    """Get user's location based on runtime context (demo)."""
    return "Florida" if runtime.context.user_id == "1" else "San Francisco"

@tool
def get_weather_for_location(city: str) -> str:
    """Get weather for a city (demo)."""
    if city.lower() in ("florida", "miami"):
        return "Sunny, 27°C, light breeze."
    if city.lower() in ("sf", "san francisco"):
        return "Foggy, 14°C, cool wind."
    return f"Partly cloudy in {city}."

ALL_TOOLS = [
    public_search,
    private_search,
    advanced_search,
    get_user_location,
    get_weather_for_location,
]


# =========================
# 4) Structured output schema (LangChain 1.x -> parse JSON yourself)
# =========================
@dataclass
class ResponseFormat:
    punny_response: str
    weather_conditions: str | None = None


def parse_responseformat_from_messages(resp: dict) -> ResponseFormat | None:
    msgs = resp.get("messages", [])
    if not msgs:
        return None
    last = msgs[-1]
    text = getattr(last, "content", "")
    try:
        obj = json.loads(text)
        return ResponseFormat(**obj)
    except Exception:
        # Not JSON or schema mismatch
        return None


# =========================
# 5) System prompt (force JSON only)
# =========================
BASE_SYSTEM_PROMPT = """You are an expert weather forecaster, who speaks in puns.

You have tools:
- get_user_location: use when user implies "outside / here" without a city
- get_weather_for_location: use to get weather for a city
- public_search/private_search/advanced_search: use for lookup questions

Rules:
1) Use tools only when needed.
2) If user asks about weather without a city, call get_user_location first.
3) If the user is NOT asking about weather (e.g. "thank you"), do NOT call weather tools.
4) Return ONLY valid JSON matching:
{"punny_response": string, "weather_conditions": string|null}
No extra text outside JSON.
"""


# =========================
# 6) Middleware: tool selection + system message injection + tool monitoring
# =========================
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

def select_relevant_tools(state: dict, runtime) -> List:
    """
    Dynamic tool subset selection.
    - Weather intent -> location + weather
    - Search intent -> public_search (+ advanced_search if user asks deep)
    - Private intent -> private_search only if user says 'auth ok' (simple demo)
    """
    text = _latest_user_text(state).lower()

    wants_weather = any(k in text for k in ["weather", "forecast", "temperature", "rain", "sunny", "outside"])
    wants_search = any(k in text for k in ["search", "look up", "find", "google", "papers", "reference"])
    wants_deep = any(k in text for k in ["deep", "advanced", "thorough", "survey"])
    auth_hint = any(k in text for k in ["auth ok", "authenticated", "login ok"])

    chosen = []

    if wants_weather:
        chosen += [get_user_location, get_weather_for_location]

    if wants_search:
        chosen += [public_search]
        if wants_deep:
            chosen += [advanced_search]
        if auth_hint:
            chosen += [private_search]

    # If nothing matches, keep at least a safe tool
    if not chosen:
        chosen = [public_search]

    # Dedup while preserving order
    seen = set()
    uniq = []
    for t in chosen:
        if t.name not in seen:
            uniq.append(t)
            seen.add(t.name)
    return uniq


@wrap_model_call
def dynamic_tool_subset(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    relevant = select_relevant_tools(request.state, request.runtime)
    names = [t.name for t in relevant]
    print(f"[ToolSelect] enabled tools: {names}")
    return handler(request.override(tools=relevant))


@wrap_model_call
def add_runtime_context_to_system(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    """
    Inject extra system info each turn (context + conversation length).
    Works across versions by creating a fresh SystemMessage.
    """
    user_id = getattr(getattr(request.runtime, "context", None), "user_id", None)
    turns = len(request.state.get("messages", []))

    extra = f"\n\n[RuntimeContext] user_id={user_id}, turns={turns}\n"
    sys_text = BASE_SYSTEM_PROMPT + extra

    return handler(request.override(system_message=SystemMessage(content=sys_text)))


@wrap_tool_call
def monitor_tool_calls(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage],
) -> ToolMessage:
    name = request.tool_call.get("name")
    args = request.tool_call.get("args")
    print(f"[ToolCall] {name} args={args}")
    try:
        out = handler(request)
        # ToolMessage content can be large; truncate print
        content = getattr(out, "content", "")
        print(f"[ToolOK] {name} -> {content[:200]}")
        return out
    except Exception as e:
        print(f"[ToolERR] {name} -> {repr(e)}")
        raise


# =========================
# 7) Model + Agent (state store + runtime context)
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
    tools=ALL_TOOLS,  # pre-register everything
    middleware=[add_runtime_context_to_system, dynamic_tool_subset, monitor_tool_calls],
    checkpointer=checkpointer,
    context_schema=Context,  # enable ToolRuntime[Context]
)

# =========================
# 8) Run demo
# =========================
config = {"configurable": {"thread_id": "1"}}
ctx = Context(user_id="1")

resp1 = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
    config=config,
    context=ctx,
)
parsed1 = parse_responseformat_from_messages(resp1)
print("\n[turn1 parsed]" if parsed1 else "\n[turn1 raw]")
print(parsed1 or resp1["messages"][-1].content)

resp2 = agent.invoke(
    {"messages": [{"role": "user", "content": "thank you!"}]},
    config=config,
    context=ctx,
)
parsed2 = parse_responseformat_from_messages(resp2)
print("\n[turn2 parsed]" if parsed2 else "\n[turn2 raw]")
print(parsed2 or resp2["messages"][-1].content)

resp3 = agent.invoke(
    {"messages": [{"role": "user", "content": "search papers about analog encryption intercepted by NATO, do a deep search"}]},
    config=config,
    context=ctx,
)
parsed3 = parse_responseformat_from_messages(resp3)
print("\n[turn3 parsed]" if parsed3 else "\n[turn3 raw]")
print(parsed3 or resp3["messages"][-1].content)
