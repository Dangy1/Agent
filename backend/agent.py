"""Compatibility shim for the O-RAN AgentRIC LangGraph entrypoint.

New project layout:
- oran_agent/graph.py
- oran_agent/config/
- oran_agent/nodes/
- oran_agent/tools/
"""

from oran_agent.core import *  # noqa: F401,F403
from oran_agent.graph import agent  # noqa: F401
