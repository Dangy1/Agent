from langchain.agents import create_agent

from oran_agent.llm_factory import build_chat_model

from .tools import TOOLS

UTM_PROMPT = """You are a UTM Compliance Agent.
Validate airspace authorization, corridor, geofence, weather, no-fly zones, mission time windows, and operator-license validity.
Do not issue UAV flight-control commands or network/RAN changes.
Return explicit approval status, scope, and expiry details.
"""

model, model_meta = build_chat_model(temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=UTM_PROMPT)

LLM_PROVIDER_META = model_meta.as_dict()

__all__ = ["agent", "LLM_PROVIDER_META"]
