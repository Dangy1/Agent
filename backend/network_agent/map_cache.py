from __future__ import annotations

"""Compatibility wrapper for map cache.

Map tile/NLS provider logic has been moved into the Map Service Agent package:
`services.map_agent.core.data_fetcher`.

This module keeps the historical import path used by `network_agent.api`.
"""

from services.map_agent.core.data_fetcher import MAP_DATA_FETCHER, MapDataFetcher


NetworkMapCache = MapDataFetcher
NETWORK_MAP_CACHE = MAP_DATA_FETCHER

__all__ = ["NetworkMapCache", "NETWORK_MAP_CACHE"]
