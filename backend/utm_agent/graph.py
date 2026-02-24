from langchain.agents import create_agent
from langchain_ollama import ChatOllama

from oran_agent.config.settings import MODEL, OLLAMA_URL

from .tools import TOOLS

UTM_PROMPT = """You are a UTM Compliance Agent.
Validate airspace authorization, corridor, geofence, weather, no-fly zones, mission time windows, and operator-license validity.
Do not issue UAV flight-control commands or network/RAN changes.
Return explicit approval status, scope, and expiry details.
"""

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=UTM_PROMPT)

__all__ = ["agent"]
