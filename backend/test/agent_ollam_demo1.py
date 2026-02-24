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

# Prefer 11434 (no auth)
ok_tags, detail = can_get_tags(URL_11434, headers=None)
ok_chat, detail_chat = can_post_chat(URL_11434, MODEL, headers=None)

if ok_tags and ok_chat:
    OLLAMA_URL = URL_11434
    client_kwargs = {}
    print(f"[Using] {OLLAMA_URL} (direct 11434, no auth)")
else:
    print(f"[11434 not usable] tags={detail} chat={detail_chat}")

    # Fallback 8080 with basic auth
    if not user or not pwd:
        raise RuntimeError("Missing OLLAMA_USER / OLLAMA_PASS in environment or .env")

    headers = basic_auth_headers(user, pwd)

    ok_tags2, detail2 = can_get_tags(URL_8080, headers=headers)
    ok_chat2, detail_chat2 = can_post_chat(URL_8080, MODEL, headers=headers)

    if not (ok_tags2 and ok_chat2):
        raise RuntimeError(f"8080 not usable: tags={detail2} chat={detail_chat2}")

    OLLAMA_URL = URL_8080
    client_kwargs = {"headers": headers}
    print(f"[Using] {OLLAMA_URL} (8080 with Basic Auth)")


# =========================
# 2) Agent demo (weather) using ChatOllama
# =========================
SYSTEM_PROMPT = """You are an expert weather forecaster, who speaks in puns.

Tools:
- get_weather_for_location(city): get weather for a specific location
- get_user_location(): get the user's location

If a user asks for the weather, make sure you know the location.
If the user implies "where I am", call get_user_location first.

If the user is NOT asking about weather (e.g. "thank you"),
reply politely with a pun and set weather_conditions to null.
"""

@dataclass
class Context:
    user_id: str

@tool
def get_weather_for_location(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

@tool
def get_user_location(runtime: ToolRuntime[Context]) -> str:
    """Retrieve user location based on user ID in runtime context."""
    user_id = runtime.context.user_id
    return "Florida" if user_id == "1" else "SF"

@dataclass
class ResponseFormat:
    punny_response: str
    weather_conditions: str | None = None


def print_structured_or_fallback(resp: dict, label: str = "") -> None:
    """Works across versions and when parsing fails."""
    # common keys seen across versions
    for key in ("structured_response", "structured_output", "output", "result"):
        if key in resp and resp[key] is not None:
            print(f"\n[{label}] {key}:")
            print(resp[key])
            return

    # fallback: print last assistant message content
    print(f"\n[{label}] No structured key found. Available keys: {list(resp.keys())}")
    msgs = resp.get("messages", [])
    if msgs:
        last = msgs[-1]
        content = getattr(last, "content", last)
        print("[last message]:", content)
    else:
        print("[raw resp]:", resp)


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
    tools=[get_user_location, get_weather_for_location],
    context_schema=Context,
    response_format=ToolStrategy(ResponseFormat),
    checkpointer=checkpointer,
)

config = {"configurable": {"thread_id": "1"}}

response = agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather outside?"}]},
    config=config,
    context=Context(user_id="1"),
)
print_structured_or_fallback(response, label="turn1")

response2 = agent.invoke(
    {"messages": [{"role": "user", "content": "thank you!"}]},
    config=config,
    context=Context(user_id="1"),
)
print_structured_or_fallback(response2, label="turn2")
