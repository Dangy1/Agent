"""Network-domain API routes for mission/network optimization controls."""

from __future__ import annotations

from fastapi import APIRouter

from .api_shared import *  # noqa: F401,F403

router = APIRouter()


@router.get("/api/network/mission/state")
def get_network_mission_state(airspace_segment: str = "sector-A3", selected_uav_id: Optional[str] = None) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.get_state(airspace_segment=airspace_segment, selected_uav_id=selected_uav_id)
    if isinstance(result, dict):
        result["sync"] = UAV_DB.get_sync()
    return result


@router.post("/api/network/mission/tick")
def post_network_mission_tick(payload: NetworkTickPayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.tick(steps=payload.steps)
    sync = _log_uav_action("network_tick", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/network/optimize")
def post_network_optimize(payload: NetworkOptimizePayload) -> Dict[str, Any]:
    result = NETWORK_MISSION_SERVICE.apply_optimization(
        mode=payload.mode,
        coverage_target_pct=payload.coverage_target_pct,
        max_tx_cap_dbm=payload.max_tx_cap_dbm,
        qos_priority_weight=payload.qos_priority_weight,
    )
    sync = _log_uav_action("network_optimize", payload=payload.model_dump(), result=result)
    if isinstance(result, dict):
        result["sync"] = sync
    return result


@router.post("/api/network/base-station/update")
def post_network_base_station_update(payload: NetworkBaseStationUpdatePayload) -> Dict[str, Any]:
    updates = payload.model_dump(exclude_none=True)
    bs_id = str(updates.pop("bs_id"))
    result = NETWORK_MISSION_SERVICE.update_base_station(bs_id, **updates)
    sync = _log_uav_action("network_bs_update", payload=payload.model_dump(), result=result, entity_id=bs_id)
    if isinstance(result, dict):
        result["sync"] = sync
    return result
