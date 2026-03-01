from __future__ import annotations

from langchain.agents import create_agent

from oran_agent.llm_factory import build_chat_model

from .copilot_workflow import copilot_workflow, run_copilot_workflow
from .tools import TOOLS

UAV_PROMPT = """You are a UAV Flight Planner Agent with a local flight simulator.
Use UAV simulator tools to plan routes, submit route to UTM geofence check, request UTM approval, launch, step the mission, and inspect status.
Preferred sequence: plan route -> submit route to UTM geofence check -> request UTM approval -> launch -> step/status.
If launch is requested without approval, request UTM approval first.
If the user asks to avoid or re-route around no-fly zones, first use the UTM/NFZ-aware replan tool so the route is adjusted using current UTM no-fly-zone data, then summarize what changed and re-run geofence/approval checks as needed.
For network/RAN slice/tc/kpm changes, delegate to the network or mission supervisor agent.
Always prefer safe states (hold, RTH, land) on uncertainty.
"""

model, model_meta = build_chat_model(temperature=0)
agent = create_agent(model=model, tools=TOOLS, system_prompt=UAV_PROMPT)

LLM_PROVIDER_META = model_meta.as_dict()

__all__ = ["agent", "copilot_workflow", "run_copilot_workflow", "LLM_PROVIDER_META"]
