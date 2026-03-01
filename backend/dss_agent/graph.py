from langchain.agents import create_agent
from langchain_ollama import ChatOllama

from oran_agent.config.settings import MODEL, OLLAMA_URL

from .tools import TOOLS

DSS_PROMPT = """You are a DSS Coordination Agent.
Manage operational intents, subscriptions, participants, and notification acknowledgment.
Do not issue direct UAV flight-control commands.
Report conflict status, blocking/advisory distinctions, and conformance outcomes clearly.
"""

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=DSS_PROMPT)

__all__ = ["agent"]
