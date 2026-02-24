import os
import base64
import requests
from dotenv import load_dotenv
from langchain_ollama import ChatOllama

from typing import List

from langchain.messages import AIMessage
from langchain.tools import tool


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

def can_get_tags(base_url: str, headers: dict | None = None) -> tuple[bool, str]:
    """Return (ok, detail)."""
    try:
        r = requests.get(f"{base_url}/api/tags", headers=headers, timeout=5)
        if r.status_code == 200:
            return True, "OK"
        return False, f"GET /api/tags -> {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"GET /api/tags exception: {repr(e)}"

# 1) Try direct Ollama first (11434) with no auth
ok, detail = can_get_tags(URL_11434, headers=None)
if ok:
    OLLAMA_URL = URL_11434
    client_kwargs = {}  # no headers
    print(f"[Using] {OLLAMA_URL} (direct 11434, no auth)")
else:
    print(f"[11434 not usable] {detail}")

    # 2) Fallback to 8080 with Basic Auth
    if not user or not pwd:
        raise RuntimeError("Missing OLLAMA_USER / OLLAMA_PASS in environment or .env")

    headers = basic_auth_headers(user, pwd)
    ok2, detail2 = can_get_tags(URL_8080, headers=headers)
    if not ok2:
        raise RuntimeError(f"8080 not usable with Basic Auth: {detail2}")

    OLLAMA_URL = URL_8080
    client_kwargs = {"headers": headers}
    print(f"[Using] {OLLAMA_URL} (8080 with Basic Auth)")


@tool
def validate_user(user_id: int, addresses: List[str]) -> bool:
    """Validate user using historical addresses.

    Args:
        user_id (int): the user ID.
        addresses (List[str]): Previous addresses as a list of strings.
    """
    return True

# Create LLM
llm = ChatOllama(
    model=MODEL,
    base_url=OLLAMA_URL,
    temperature=0,
    client_kwargs=client_kwargs,
).bind_tools([validate_user])

result = llm.invoke(
    "Could you validate user 123? They previously lived at "
    "123 Fake St in Boston MA and 234 Pretend Boulevard in "
    "Houston TX."
)

print(result.content)

'''
# Test calls
try:
    resp1 = llm.invoke("Say hello. What model are you?")
    print(resp1.content)

    resp2 = llm.invoke("Explain what LangChain is in one paragraph.")
    print(resp2.content)

except Exception as e:
    print("Error during invoke:", repr(e))
    raise
'''



