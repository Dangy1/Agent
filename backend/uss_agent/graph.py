from langchain.agents import create_agent
from langchain_ollama import ChatOllama

from oran_agent.config.settings import MODEL, OLLAMA_URL

from .tools import TOOLS

USS_PROMPT = """You are a USS Agent.
Publish and query operational intents through DSS-compatible workflows.
Manage subscriptions and notification acknowledgments for your USS identity.
Do not issue direct UAV actuation commands.
"""

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=USS_PROMPT)

__all__ = ["agent"]
