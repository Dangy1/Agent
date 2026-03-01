from langchain.agents import create_agent

from oran_agent.llm_factory import build_chat_model

from .tools import TOOLS

NETWORK_PROMPT = """You are a Network Deployment Agent for O-RAN/FlexRIC operations.
Operate slice/tc/kpm_rc tools only.
Verify outcomes with health/status/log tools after changes.
Do not make UAV flight decisions or UTM compliance decisions.
"""

model, model_meta = build_chat_model(temperature=0)

agent = create_agent(model=model, tools=TOOLS, system_prompt=NETWORK_PROMPT)

LLM_PROVIDER_META = model_meta.as_dict()

__all__ = ["agent", "LLM_PROVIDER_META"]
