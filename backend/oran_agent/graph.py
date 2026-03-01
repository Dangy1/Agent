from langchain.agents import create_agent

from oran_agent.llm_factory import build_chat_model
from oran_agent.core import BASE_SYSTEM_PROMPT
from oran_agent.nodes.state import Context, CustomState
from oran_agent.tools.mcp import TOOLS

model, model_meta = build_chat_model(temperature=0)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
)

LLM_PROVIDER_META = model_meta.as_dict()

__all__ = ["agent", "Context", "CustomState", "LLM_PROVIDER_META"]
