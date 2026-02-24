import os
import base64
import requests
from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

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

ok_tags, _ = can_get_tags(URL_11434, headers=None)
ok_chat, _ = can_post_chat(URL_11434, MODEL, headers=None)

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
# 1) Tools
# =========================
USER_ID = "1"  # "runtime context" for this demo; replace with your own session/auth context

@tool
def get_user_location() -> str:
    """Return the user's location based on a demo user_id."""
    return "Florida" if USER_ID == "1" else "San Francisco"

@tool
def get_weather_for_location(city: str) -> str:
    """Get weather for a given city (stub)."""
    if city.lower() in ("florida", "miami"):
        return "Sunny, 27°C, light breeze."
    if city.lower() in ("sf", "san francisco"):
        return "Foggy, 14°C, cool wind."
    return f"Partly cloudy in {city}."

@tool
def calculate_tip(bill_amount: float, tip_percentage: float = 20.0) -> str:
    """Calculate tip for a bill."""
    tip = bill_amount * (tip_percentage / 100.0)
    total = bill_amount + tip
    return f"Tip: ${tip:.2f}, Total: ${total:.2f}"


BASE_TOOLS = [get_user_location, get_weather_for_location]
DYNAMIC_TOOLS = [calculate_tip]


def enable_tools_for_text(text: str):
    """Dynamic tool selection: add calculate_tip only when relevant."""
    text_l = text.lower()
    wants_tip = any(k in text_l for k in ["tip", "bill", "total", "gratuity", "%"])
    tools = list(BASE_TOOLS)
    if wants_tip:
        tools += DYNAMIC_TOOLS
        print("[DynamicTools] calculate_tip enabled")
    return tools


# =========================
# 2) Model (bind tools per-turn)
# =========================
SYSTEM_PROMPT = """You are an assistant that uses tools in a ReAct loop.

Rules:
- If user asks weather without city (e.g., "outside/here"), call get_user_location first.
- Then call get_weather_for_location(city).
- If user asks about bill/tip/total, use calculate_tip.
- Be concise in the final answer.
"""

def make_llm(tools):
    # bind_tools tells the model which tool schemas are available
    return ChatOllama(
        model=MODEL,
        base_url=OLLAMA_URL,
        temperature=0,
        client_kwargs=client_kwargs,
    ).bind_tools(tools)


# =========================
# 3) ReAct Tool Loop (Human <-> AI)
# =========================
def run_react_turn(history, user_text: str, max_steps: int = 8):
    """
    One user turn processed by: LLM -> (tool calls?) -> tool exec -> LLM -> ... until final.
    Returns updated history.
    """
    tools = enable_tools_for_text(user_text)
    llm = make_llm(tools)

    # tool registry for execution
    tool_by_name = {t.name: t for t in tools}

    # Add human message
    history.append(HumanMessage(content=user_text))

    # Prepend system prompt each turn (simple & robust)
    messages = [HumanMessage(content=SYSTEM_PROMPT)] + history

    for step in range(1, max_steps + 1):
        ai: AIMessage = llm.invoke(messages)
        messages.append(ai)
        history.append(ai)

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            # Final answer
            return history

        # Execute each tool call, append ToolMessage(s)
        for call in tool_calls:
            name = call.get("name")
            args = call.get("args", {}) or {}
            call_id = call.get("id", "")

            print(f"[ToolCall step={step}] {name} args={args}")

            tool = tool_by_name.get(name)
            if tool is None:
                # Tool not available (dynamic tool filtering mismatch)
                tm = ToolMessage(
                    content=f"Tool error: '{name}' is not enabled for this request.",
                    tool_call_id=call_id,
                )
                messages.append(tm)
                history.append(tm)
                continue

            try:
                result = tool.invoke(args)
                tm = ToolMessage(content=str(result), tool_call_id=call_id)
                print(f"[ToolOK step={step}] {name} -> {str(result)[:200]}")
            except Exception as e:
                tm = ToolMessage(
                    content=f"Tool error in {name}: {str(e)}",
                    tool_call_id=call_id,
                )
                print(f"[ToolERR step={step}] {name} -> {repr(e)}")

            messages.append(tm)
            history.append(tm)

    # If we hit max steps
    history.append(AIMessage(content="I hit the tool-step limit; please rephrase or simplify."))
    return history


def chat():
    history = []
    print("ReAct chat started. Type 'exit' to quit.\n")

    while True:
        user_text = input("Human: ").strip()
        if user_text.lower() in ("exit", "quit"):
            break

        history = run_react_turn(history, user_text)

        # Print the last AI final message content
        # (might be a ToolMessage at end if tool loop ended abruptly)
        last_ai = None
        for m in reversed(history):
            if isinstance(m, AIMessage):
                last_ai = m
                break
        print("AI:", (last_ai.content if last_ai else "<no AI message>"), "\n")


if __name__ == "__main__":
    chat()
