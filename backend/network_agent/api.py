"""Network mission API (separate process/port) for BS coverage and UAV/UTM tracking overlays."""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
except Exception as e:  # pragma: no cover
    raise RuntimeError("network_agent.api requires fastapi and pydantic") from e

from services.map_agent.map_agent_api import MAP_SERVICE_AGENT

from .service import NETWORK_MISSION_SERVICE
from agent_db import AgentDB


class NetworkTickPayload(BaseModel):
    steps: int = 1


class NetworkOptimizePayload(BaseModel):
    mode: str = "coverage"
    coverage_target_pct: float = 96.0
    max_tx_cap_dbm: float = 41.0
    qos_priority_weight: float = 68.0


class NetworkBaseStationUpdatePayload(BaseModel):
    bs_id: str
    txPowerDbm: Optional[float] = None
    tiltDeg: Optional[float] = None
    loadPct: Optional[float] = None
    status: Optional[str] = None


class NetworkTelemetryIngestPayload(BaseModel):
    payload: Dict[str, Any]


class NetworkResetPayload(BaseModel):
    clear_live_telemetry: bool = True
    keep_base_stations: bool = True


class NetworkMapPrefetchPayload(BaseModel):
    provider: str = "all"
    center_lon: Optional[float] = None
    center_lat: Optional[float] = None
    radius_km: Optional[float] = None
    zoom_min: Optional[int] = None
    zoom_max: Optional[int] = None
    force_refresh: bool = False


class MapPlotPointPayload(BaseModel):
    id: str
    lat: float
    lon: float
    alt: float = 0.0
    geoid_sep_m: Optional[float] = None
    scope: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MapToggleViewPayload(BaseModel):
    engine: str


class MapSyncPublishPayload(BaseModel):
    source: str = "client"
    topic: str = "custom"
    scope: str = "shared"
    payload: Dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Network Mission API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NETWORK_DB = AgentDB("network")


def _restore_network_state() -> None:
    state = NETWORK_DB.get_state("service")
    if isinstance(state, dict):
        NETWORK_MISSION_SERVICE.load_state(state)


def _persist_network_state() -> None:
    NETWORK_DB.set_state("service", NETWORK_MISSION_SERVICE.export_state())


def _safe_publish_network_map_sync(airspace_segment: str = "sector-A3", selected_uav_id: Optional[str] = None) -> None:
    try:
        state = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=selected_uav_id)
        if isinstance(state, dict) and str(state.get("status", "")).lower() == "success" and isinstance(state.get("result"), dict):
            MAP_SERVICE_AGENT.sync_network_state(state.get("result"), scope="shared", source="network")
    except Exception:
        # Keep business APIs resilient even if map sync fails.
        pass


def _log_network_action(action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    _persist_network_state()
    sync = NETWORK_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)
    _safe_publish_network_map_sync()
    return sync


def _map_config_result(request: Request) -> Dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    return {"status": "success", "result": MAP_SERVICE_AGENT.public_config(base)}


def _map_cache_prefetch_result(payload: NetworkMapPrefetchPayload) -> Dict[str, Any]:
    try:
        result = MAP_SERVICE_AGENT.prefetch(
            provider=payload.provider,
            center_lon=payload.center_lon,
            center_lat=payload.center_lat,
            radius_km=payload.radius_km,
            zoom_min=payload.zoom_min,
            zoom_max=payload.zoom_max,
            force_refresh=bool(payload.force_refresh),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sync = _log_network_action("map_cache_prefetch", payload=payload.model_dump(), result=result)
    return {"status": "success", "result": {**result, "sync": sync}}


def _map_tile_response(provider: str, z: int, x: int, y: int) -> Response:
    try:
        tile = MAP_SERVICE_AGENT.get_tile(provider=provider, z=int(z), x=int(x), y=int(y))
    except ValueError as e:
        detail = str(e)
        if "provider_unavailable" in detail:
            raise HTTPException(status_code=503, detail=detail) from e
        raise HTTPException(status_code=400, detail=detail) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream_tile_fetch_failed:{provider}:{z}/{x}/{y}") from e
    cache_hit = bool(tile.get("cache_hit"))
    headers = {
        "Cache-Control": "public, max-age=604800",
        "X-Map-Cache": "HIT" if cache_hit else "MISS",
        "X-Map-Provider": str(tile.get("provider", provider)),
    }
    return Response(content=tile["content"], media_type=str(tile.get("media_type", "application/octet-stream")), headers=headers)


_restore_network_state()


@app.get("/api/network/mission/state")
def get_network_mission_state(airspace_segment: str = "sector-A3", selected_uav_id: Optional[str] = None) -> Dict[str, Any]:
    resp = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=selected_uav_id)
    if isinstance(resp, dict):
        resp["sync"] = NETWORK_DB.get_sync()
    _safe_publish_network_map_sync(airspace_segment=airspace_segment, selected_uav_id=selected_uav_id)
    return resp


@app.get("/api/network/sync")
def get_network_sync(limit_actions: int = 5) -> Dict[str, Any]:
    return {
        "status": "success",
        "result": {
            "sync": NETWORK_DB.get_sync(),
            "recentActions": NETWORK_DB.recent_actions(limit_actions),
        },
    }


@app.get("/api/network/telemetry/source")
def get_network_telemetry_source() -> Dict[str, Any]:
    return {"status": "success", "result": NETWORK_MISSION_SERVICE.traffic_source_config()}


@app.post("/api/network/telemetry/ingest")
def post_network_telemetry_ingest(payload: NetworkTelemetryIngestPayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.ingest_live_telemetry(payload.payload)
    sync = _log_network_action("telemetry_ingest", payload={"hasPayload": True}, result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/network/mission/tick")
def post_network_mission_tick(payload: NetworkTickPayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.tick(steps=payload.steps)
    sync = _log_network_action("mission_tick", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/network/optimize")
def post_network_optimize(payload: NetworkOptimizePayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.apply_optimization(
        mode=payload.mode,
        coverage_target_pct=payload.coverage_target_pct,
        max_tx_cap_dbm=payload.max_tx_cap_dbm,
        qos_priority_weight=payload.qos_priority_weight,
    )
    sync = _log_network_action("optimize", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/network/base-station/update")
def post_network_base_station_update(payload: NetworkBaseStationUpdatePayload) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    bs_id = str(updates.pop("bs_id"))
    result = NETWORK_MISSION_SERVICE.update_base_station(bs_id, **updates)
    sync = _log_network_action("base_station_update", payload=payload.model_dump(), result=result, entity_id=bs_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@app.post("/api/network/mission/reset")
def post_network_mission_reset(payload: NetworkResetPayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.reset_runtime(
        clear_live_telemetry=bool(payload.clear_live_telemetry),
        keep_base_stations=bool(payload.keep_base_stations),
    )
    sync = _log_network_action("mission_reset", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


# Map Service Agent API (new decoupled endpoints)
@app.get("/api/map/config")
def get_map_config(request: Request) -> Dict[str, Any]:
    return _map_config_result(request)


@app.get("/api/map/cache/status")
def get_map_cache_status() -> Dict[str, Any]:
    return {"status": "success", "result": MAP_SERVICE_AGENT.cache_status()}


@app.post("/api/map/cache/prefetch")
def post_map_cache_prefetch(payload: NetworkMapPrefetchPayload) -> Dict[str, Any]:
    return _map_cache_prefetch_result(payload)


@app.get("/api/map/tiles/{provider}/{z}/{x}/{y}")
def get_map_tile(provider: str, z: int, x: int, y: int) -> Response:
    return _map_tile_response(provider, z, x, y)


@app.post("/api/map/plot-point")
def post_map_plot_point(payload: MapPlotPointPayload) -> Dict[str, Any]:
    raw = payload.model_dump()
    try:
        result = MAP_SERVICE_AGENT.plot_point(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sync = _log_network_action("map_plot_point", payload=raw, result=result, entity_id=payload.id)
    return {"status": "success", "result": {**result, "sync": sync}}


@app.get("/api/map/bounds")
def get_map_bounds() -> Dict[str, Any]:
    return {"status": "success", "result": MAP_SERVICE_AGENT.current_bounds()}


@app.get("/api/map/view")
def get_map_view() -> Dict[str, Any]:
    return {"status": "success", "result": MAP_SERVICE_AGENT.view_status()}


@app.put("/api/map/toggle-view")
def put_map_toggle_view(payload: MapToggleViewPayload) -> Dict[str, Any]:
    try:
        result = MAP_SERVICE_AGENT.toggle_view(payload.engine)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sync = _log_network_action("map_toggle_view", payload=payload.model_dump(), result=result)
    return {"status": "success", "result": {**result, "sync": sync}}


@app.get("/api/map/sync/state")
def get_map_sync_state(scope: str = "shared", include_shared: bool = True) -> Dict[str, Any]:
    result = MAP_SERVICE_AGENT.sync_state(scope=scope, include_shared=bool(include_shared))
    return {"status": "success", "result": result}


@app.get("/api/map/sync/events")
def get_map_sync_events(scope: Optional[str] = None, since_id: int = 0, limit: int = 100) -> Dict[str, Any]:
    result = MAP_SERVICE_AGENT.sync_events(scope=scope, since_id=since_id, limit=limit)
    return {"status": "success", "result": result}


@app.post("/api/map/sync/publish")
def post_map_sync_publish(payload: MapSyncPublishPayload) -> Dict[str, Any]:
    result = MAP_SERVICE_AGENT.sync_publish(
        source=payload.source,
        topic=payload.topic,
        scope=payload.scope,
        payload=payload.payload,
    )
    return {"status": "success", "result": result}


# Compatibility aliases for existing clients.
@app.get("/api/network/map/config")
def get_network_map_config(request: Request) -> Dict[str, Any]:
    return _map_config_result(request)


@app.get("/api/network/map/cache/status")
def get_network_map_cache_status() -> Dict[str, Any]:
    return get_map_cache_status()


@app.post("/api/network/map/cache/prefetch")
def post_network_map_cache_prefetch(payload: NetworkMapPrefetchPayload) -> Dict[str, Any]:
    return _map_cache_prefetch_result(payload)


@app.get("/api/network/map/tiles/{provider}/{z}/{x}/{y}")
def get_network_map_tile(provider: str, z: int, x: int, y: int) -> Response:
    return _map_tile_response(provider, z, x, y)


@app.post("/api/network/map/plot-point")
def post_network_map_plot_point(payload: MapPlotPointPayload) -> Dict[str, Any]:
    return post_map_plot_point(payload)


@app.get("/api/network/map/bounds")
def get_network_map_bounds() -> Dict[str, Any]:
    return get_map_bounds()


@app.put("/api/network/map/toggle-view")
def put_network_map_toggle_view(payload: MapToggleViewPayload) -> Dict[str, Any]:
    return put_map_toggle_view(payload)


@app.get("/api/network/map/sync/state")
def get_network_map_sync_state(scope: str = "shared", include_shared: bool = True) -> Dict[str, Any]:
    return get_map_sync_state(scope=scope, include_shared=include_shared)


@app.get("/api/network/map/sync/events")
def get_network_map_sync_events(scope: Optional[str] = None, since_id: int = 0, limit: int = 100) -> Dict[str, Any]:
    return get_map_sync_events(scope=scope, since_id=since_id, limit=limit)


@app.post("/api/network/map/sync/publish")
def post_network_map_sync_publish(payload: MapSyncPublishPayload) -> Dict[str, Any]:
    return post_map_sync_publish(payload)
