from langchain.agents import create_agent
from langchain_ollama import ChatOllama

from oran_agent.config.settings import MODEL, OLLAMA_URL
from oran_agent.core import BASE_SYSTEM_PROMPT
from oran_agent.nodes.state import Context, CustomState
from oran_agent.tools.mcp import TOOLS

model = ChatOllama(model=MODEL, base_url=OLLAMA_URL, temperature=0)

agent = create_agent(
    model=model,
    tools=TOOLS,
    system_prompt=BASE_SYSTEM_PROMPT,
    state_schema=CustomState,
)

__all__ = ["agent", "Context", "CustomState"]
