from langchain.agents import create_agent
from langchain_ollama import ChatOllama

from oran_agent.config.settings import MODEL, OLLAMA_URL

from .tools import TOOLS

NETWORK_PROMPT = """You are a Network Deployment Agent for O-RAN/FlexRIC operations.
Operate slice/tc/kpm_rc tools only.
Verify outcomes with health/status/log tools after changes.
Do not make UAV flight decisions or UTM compliance decisions.
"""

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=NETWORK_PROMPT)

__all__ = ["agent"]

