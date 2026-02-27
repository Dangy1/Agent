"""UAV + UTM simulator API module assembly.

This facade keeps the historical import path (`uav_agent.api`) while the implementation is
split across shared helpers/state and domain route modules for readability.
"""

from __future__ import annotations

from .api_shared import *  # noqa: F401,F403
from .api_shared import app

from . import api_routes_network as _api_routes_network
from . import api_routes_uav as _api_routes_uav
from . import api_routes_utm as _api_routes_utm

app.include_router(_api_routes_uav.router)
app.include_router(_api_routes_utm.router)
app.include_router(_api_routes_network.router)
