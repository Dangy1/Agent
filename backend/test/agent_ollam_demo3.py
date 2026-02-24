import os
import base64
import requests
from dataclasses import dataclass
from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy


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
# 2) Tools (pre-registered) using runtime context (and optional state)
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
    """Show what the agent knows about this session (context + thread state if available)."""
    uid = runtime.context.user_id

    # Depending on LangChain version, ToolRuntime may or may not expose state.
    # We'll try to read it safely.
    state = getattr(runtime, "state", None)
    keys = list(state.keys()) if isinstance(state, dict) else []
    return f"user_id={uid}, state_keys={keys}"

TOOLS = [get_user_location, get_weather_for_location, session_info]


# =========================
# 3) System prompt + structured response
# =========================
SYSTEM_PROMPT = """You are an expert weather forecaster who speaks in puns.

You have tools:
- get_user_location: use to get the user's location (when user implies 'here' / 'outside')
- get_weather_for_location: use to get weather for a specific city
- session_info: shows session context info

Rules:
1) If the user asks for weather without naming a city, call get_user_location first.
2) Only include weather_conditions if the user asked about weather.
3) If the user says 'thanks' or is not asking about weather, reply with a pun and set weather_conditions to null.
Return your final answer in the required schema.
"""

@dataclass
class ResponseFormat:
    punny_response: str
    weather_conditions: str | None = None


def print_structured_or_fallback(resp: dict, label: str = "") -> None:
    """Avoid KeyError across versions / occasional parse failures."""
    if "structured_response" in resp and resp["structured_response"] is not None:
        print(f"\n[{label}] structured_response:\n{resp['structured_response']}")
        return

    # fallback: show keys + last message
    print(f"\n[{label}] No structured_response. Keys={list(resp.keys())}")
    msgs = resp.get("messages", [])
    if msgs:
        last = msgs[-1]
        print("[last message]:", getattr(last, "content", last))
    else:
        print("[raw]:", resp)


# =========================
# 4) Model + state store (checkpointing)
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
    tools=TOOLS,                          # <-- pre-registered tools here
    context_schema=Context,               # <-- runtime context schema
    response_format=ToolStrategy(ResponseFormat),
    checkpointer=checkpointer,            # <-- state store (per thread_id)
)


# =========================
# 5) Run: same thread_id -> state persists
# =========================
config = {"configurable": {"thread_id": "demo-thread-1"}}
ctx = Context(user_id="1")

resp1 = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
    config=config,
    context=ctx,
)
print_structured_or_fallback(resp1, label="turn1")

resp2 = agent.invoke(
    {"messages": [{"role": "user", "content": "thank you!"}]},
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
