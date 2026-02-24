import os
import base64
import requests
from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage

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
# 1) Tools
# =========================
@tool
def search_products(query: str) -> list[dict]:
    """
    Search for popular products (DEMO).
    Replace this stub with a real search API (Tavily/SerpAPI/retailer API).
    Return: list of {name, brand, retailer}.
    """
    q = query.lower()
    if "wireless" in q and "headphone" in q:
        return [
            {"name": "Sony WH-1000XM5", "brand": "Sony", "retailer": "Amazon"},
            {"name": "Bose QuietComfort Ultra Headphones", "brand": "Bose", "retailer": "Best Buy"},
            {"name": "Apple AirPods Pro (2nd gen)", "brand": "Apple", "retailer": "Apple"},
            {"name": "Sennheiser Momentum 4 Wireless", "brand": "Sennheiser", "retailer": "Amazon"},
        ]
    return [{"name": "Unknown", "brand": "Unknown", "retailer": "Unknown"}]

@tool
def check_stock(product_name: str, retailer: str) -> dict:
    """
    Check availability (DEMO).
    Replace with real calls:
      - retailer API
      - scraping with permissions
      - your internal inventory service
    Return: {in_stock: bool, note: str}
    """
    # Demo logic: pretend Amazon/Apple are in stock, Best Buy maybe limited
    key = (retailer or "").lower()
    if "amazon" in key or "apple" in key:
        return {"in_stock": True, "note": f"{retailer}: In stock (demo)"}
    if "best buy" in key:
        return {"in_stock": False, "note": f"{retailer}: Out of stock / limited (demo)"}
    return {"in_stock": False, "note": f"{retailer}: Unknown availability (demo)"}


TOOLS = [search_products, check_stock]


# =========================
# 2) ReAct loop driver
# =========================
SYSTEM_PROMPT = """You are an agent that follows the ReAct pattern.

You MUST:
- Use tools when freshness matters (popularity, availability, stock).
- Think briefly, then act by calling tools.
- Use observations from tools before deciding final answer.
- End with a concise final answer.

Return your final answer as normal text (not JSON).
"""

def make_llm():
    return ChatOllama(
        model=MODEL,
        base_url=OLLAMA_URL,
        temperature=0,
        client_kwargs=client_kwargs,
    ).bind_tools(TOOLS)

def react_loop(user_query: str, max_steps: int = 8) -> str:
    llm = make_llm()
    tool_by_name = {t.name: t for t in TOOLS}

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_query),
    ]

    for step in range(1, max_steps + 1):
        ai: AIMessage = llm.invoke(messages)
        messages.append(ai)

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            return ai.content or "(no content)"

        # Execute tool calls
        for call in tool_calls:
            name = call.get("name")
            args = call.get("args", {}) or {}
            call_id = call.get("id", "")

            print(f"[Act step={step}] {name} args={args}")

            tool = tool_by_name.get(name)
            if tool is None:
                messages.append(ToolMessage(content=f"Tool '{name}' not found.", tool_call_id=call_id))
                continue

            try:
                result = tool.invoke(args)
                messages.append(ToolMessage(content=str(result), tool_call_id=call_id))
            except Exception as e:
                # Tool error handling: feed error back as observation
                messages.append(ToolMessage(content=f"Tool error in {name}: {e}", tool_call_id=call_id))

    return "Reached max tool steps without final answer."


if __name__ == "__main__":
    question = "Find the most popular wireless headphones right now and check if they're in stock"
    answer = react_loop(question)
    print("\n=== FINAL ANSWER ===\n", answer)
