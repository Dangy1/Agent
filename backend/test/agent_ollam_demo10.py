import os
import base64
import requests
from dataclasses import dataclass
from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool, ToolRuntime
from langgraph.checkpoint.memory import InMemorySaver
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


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

def can_get_tags(base_url: str, headers: dict | None = None) -> bool:
    try:
        r = requests.get(f"{base_url}/api/tags", headers=headers, timeout=5)
        return r.status_code == 200
    except:
        return False

def can_post_chat(base_url: str, model: str, headers: dict | None = None) -> bool:
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "stream": False}
    try:
        r = requests.post(f"{base_url}/api/chat", headers=headers, json=payload, timeout=15)
        return r.status_code == 200
    except:
        return False

if can_get_tags(URL_11434) and can_post_chat(URL_11434, MODEL):
    OLLAMA_URL = URL_11434
    client_kwargs = {}
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
# 2) Tools (stubs; replace with real APIs)
# =========================
@tool
def search_products(query: str) -> list[dict]:
    """Search for popular products (stub)."""
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
def check_stock(product_name: str, retailer: str) -> dict:
    """Check availability (stub)."""
    r = retailer.lower()
    if "amazon" in r or "apple" in r:
        return {"in_stock": True, "note": f"{retailer}: In stock (demo)"}
    if "best buy" in r:
        return {"in_stock": False, "note": f"{retailer}: Out of stock / limited (demo)"}
    return {"in_stock": False, "note": f"{retailer}: Unknown availability (demo)"}

ALL_TOOLS = [search_products, check_stock]


# =========================
# 3) Helpers to inspect state
# =========================
def _state_contains_product_list(state: dict) -> bool:
    """
    Detect whether search_products has already been used in this thread.
    We inspect messages for a ToolMessage that looks like a list of dicts.
    This is deliberately simple & robust for LangChain 1.x.
    """
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        # ToolMessage in LC is an object with .type == "tool" and .content is a string
        m_type = getattr(m, "type", None)
        if m_type == "tool":
            content = getattr(m, "content", "")
            # our tool returns list[dict] -> string will contain "[{'name':" pattern
            if isinstance(content, str) and ("'name':" in content or '"name"' in content):
                # crude but works for demo
                if "Sony WH-1000XM5" in content or "AirPods" in content or "Momentum" in content:
                    return True
    return False


# =========================
# 4) Middleware: dynamic system prompt
# =========================
@wrap_model_call
def dynamic_system_prompt(request: ModelRequest, handler) -> ModelResponse:
    ctx = getattr(request.runtime, "context", None)
    user_id = getattr(ctx, "user_id", "unknown")
    locale = getattr(ctx, "locale", "en")
    persona = getattr(ctx, "persona", "shopping-assistant")
    turns = len(request.state.get("messages", []))

    has_products = _state_contains_product_list(request.state)

    sys = f"""You are a ReAct agent ({persona}).
Locale: {locale}
UserID: {user_id}
Turns so far: {turns}

Goal: Identify the most popular wireless headphones right now and verify stock.

Tool policy:
- Popularity & stock are time-sensitive. Use tools.
- If you do NOT yet have a product list, call search_products("wireless headphones") first.
- After you have products, call check_stock(product_name, retailer) for the top items.
- Use observations before answering.
- Keep reasoning brief and then provide a clear final answer.
"""
    if not has_products:
        sys += "\nState hint: You have not fetched products yet. You must search first.\n"
    else:
        sys += "\nState hint: You already have product candidates. You should check stock now.\n"

    return handler(request.override(system_message=SystemMessage(content=sys)))


# =========================
# 5) Middleware: dynamically enable/disable tools based on state
#     - Hide check_stock until we've run search_products at least once.
# =========================
@wrap_model_call
def gate_tools_by_state(request: ModelRequest, handler) -> ModelResponse:
    has_products = _state_contains_product_list(request.state)

    if not has_products:
        # Only allow search_products at the beginning
        gated = [t for t in request.tools if t.name == "search_products"]
        print("[ToolGate] products not fetched -> enabling:", [t.name for t in gated])
        return handler(request.override(tools=gated))

    # After products exist, allow both tools
    print("[ToolGate] products fetched -> enabling:", [t.name for t in request.tools])
    return handler(request)


# =========================
# 6) Model + agent (stateful invocation)
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
    tools=ALL_TOOLS,  # all tools must be registered upfront
    middleware=[
        dynamic_system_prompt,  # sets per-turn system prompt from state/context
        gate_tools_by_state,    # filters tools based on state
    ],
    checkpointer=checkpointer,  # keeps state by thread_id
    context_schema=Context,
)


# =========================
# 7) Invocation: pass a state update (messages)
# =========================
config = {"configurable": {"thread_id": "headphones-demo"}}
ctx = Context(user_id="1", locale="en", persona="shopping-assistant")

# First call: should ONLY allow search_products
resp1 = agent.invoke(
    {"messages": [{"role": "user", "content": "Find the most popular wireless headphones right now and check if they're in stock"}]},
    config=config,
    context=ctx,
)
print("\n=== ASSISTANT (turn 1) ===")
print(resp1["messages"][-1].content)

# Second call: now check_stock should be enabled automatically
resp2 = agent.invoke(
    {"messages": [{"role": "user", "content": "Great—now verify stock for the top 3 and summarize."}]},
    config=config,
    context=ctx,
)
print("\n=== ASSISTANT (turn 2) ===")
print(resp2["messages"][-1].content)
