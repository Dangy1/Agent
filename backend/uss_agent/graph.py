from langchain.agents import create_agent

from oran_agent.llm_factory import build_chat_model

from .tools import TOOLS

USS_PROMPT = """You are a USS Agent.
Publish and query operational intents through DSS-compatible workflows.
Manage subscriptions and notification acknowledgments for your USS identity.
Do not issue direct UAV actuation commands.
"""

model, model_meta = build_chat_model(temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=USS_PROMPT)

LLM_PROVIDER_META = model_meta.as_dict()

__all__ = ["agent", "LLM_PROVIDER_META"]
