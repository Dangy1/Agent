import os
import base64
import requests
from dotenv import load_dotenv
from typing import Callable

from langchain_ollama import ChatOllama
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse

# =========================
# 0) Load env
# =========================
load_dotenv()

MODEL = "gpt-oss:latest"
MODEL2 = "devstral-small-2:24b"

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
# 2) Define tools (minimal)
# =========================
@tool
def search(query: str) -> str:
    """Search for information (stub)."""
    return f"Results for: {query}"

@tool
def get_weather(location: str) -> str:
    """Get weather information for a location (stub)."""
    return f"Weather in {location}: Sunny, 72°F"

TOOLS = [search, get_weather]

# =========================
# 3) Two models + dynamic selection middleware
# =========================
basic_model = ChatOllama(
    model=MODEL,
    base_url=OLLAMA_URL,
    temperature=0,
    client_kwargs=client_kwargs,
)

advanced_model = ChatOllama(
    model=MODEL2,
    base_url=OLLAMA_URL,
    temperature=0,
    client_kwargs=client_kwargs,
)

def _latest_user_text(state: dict) -> str:
    """Best-effort extract latest user message text."""
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        role = getattr(m, "role", None) or getattr(m, "type", None)
        content = getattr(m, "content", None)
        if role in ("user", "human") and isinstance(content, str):
            return content
        # sometimes dict messages are used
        if isinstance(m, dict) and m.get("role") == "user":
            return m.get("content", "")
    return ""

def _should_use_advanced(state: dict) -> bool:
    """Heuristic: long/complex prompt or many turns -> advanced model."""
    text = _latest_user_text(state).lower()
    turns = len(state.get("messages", []))

    complex_keywords = ["code", "error", "traceback", "stack", "slurm", "docker", "nginx", "python", "regex", "math", "equation"]
    looks_complex = (len(text) > 400) or any(k in text for k in complex_keywords) or (turns > 12)
    return looks_complex

@wrap_model_call
def dynamic_model_selection(request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
    """Choose model dynamically, then continue pipeline."""
    chosen = advanced_model if _should_use_advanced(request.state) else basic_model

    # Optional: log which model is chosen
    chosen_name = MODEL2 if chosen is advanced_model else MODEL
    print(f"[ModelSelect] using: {chosen_name}")

    return handler(request.override(model=chosen))

# =========================
# 4) Create ONE agent with middleware
# =========================
checkpointer = InMemorySaver()

agent = create_agent(
    model=basic_model,          # default (middleware may override)
    tools=TOOLS,
    middleware=[dynamic_model_selection],
    checkpointer=checkpointer,  # keep conversation state per thread_id
)

# =========================
# 5) Run demo
# =========================
config = {"configurable": {"thread_id": "1"}}

resp1 = agent.invoke({"messages": [{"role": "user", "content": "What is the weather in Boston?"}]}, config=config)
print(resp1["messages"][-1].content)

resp2 = agent.invoke({"messages": [{"role": "user", "content": "Here is a long debugging question... " + "x"*500}]}, config=config)
print(resp2["messages"][-1].content)
