from langchain.agents import create_agent

from oran_agent.llm_factory import build_chat_model

from .tools import TOOLS

DSS_PROMPT = """You are a DSS Coordination Agent.
Manage operational intents, subscriptions, participants, and notification acknowledgment.
Do not issue direct UAV flight-control commands.
Report conflict status, blocking/advisory distinctions, and conformance outcomes clearly.
"""

model, model_meta = build_chat_model(temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=DSS_PROMPT)

LLM_PROVIDER_META = model_meta.as_dict()

__all__ = ["agent", "LLM_PROVIDER_META"]
