"""Network mission API (separate process/port) for BS coverage and UAV/UTM tracking overlays."""

from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except Exception as e:  # pragma: no cover
    raise RuntimeError("network_agent.api requires fastapi and pydantic") from e

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


def _log_network_action(action: str, *, payload: Any = None, result: Any = None, entity_id: str | None = None) -> Dict[str, Any]:
    _persist_network_state()
    return NETWORK_DB.record_action(action, payload=payload, result=result, entity_id=entity_id)


_restore_network_state()


@app.get("/api/network/mission/state")
def get_network_mission_state(airspace_segment: str = "sector-A3", selected_uav_id: Optional[str] = None) -> Dict[str, Any]:
    resp = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=selected_uav_id)
    if isinstance(resp, dict):
        resp["sync"] = NETWORK_DB.get_sync()
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
