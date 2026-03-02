"""Pydantic payload models for the UAV agent API."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .simulator import DEFAULT_ROUTE, OTANIEMI_CENTER_LAT, OTANIEMI_CENTER_LON


class WaypointModel(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    altM: Optional[float] = None
    action: Optional[str] = None
    wp_origin: Optional[str] = Field(default=None, alias="_wp_origin")
    wp_source: Optional[str] = Field(default=None, alias="_wp_source")
    mapped_from_original_index: Optional[int] = Field(default=None, alias="_mapped_from_original_index")
    mapped_from_wp_source: Optional[str] = Field(default=None, alias="_mapped_from_wp_source")

    @model_validator(mode="after")
    def _normalize_geo(self) -> "WaypointModel":
        lon = self.lon if self.lon is not None else self.x
        lat = self.lat if self.lat is not None else self.y
        alt_m = self.altM if self.altM is not None else self.z
        if lon is None or lat is None:
            raise ValueError("waypoint requires lon/lat (or x/y compatibility fields)")
        if alt_m is None:
            alt_m = 0.0
        self.lon = float(lon)
        self.lat = float(lat)
        self.altM = float(alt_m)
        self.x = float(self.lon)
        self.y = float(self.lat)
        self.z = float(self.altM)
        return self


class PositionModel(BaseModel):
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    altM: Optional[float] = None

    @model_validator(mode="after")
    def _normalize_geo(self) -> "PositionModel":
        lon = self.lon if self.lon is not None else self.x
        lat = self.lat if self.lat is not None else self.y
        alt_m = self.altM if self.altM is not None else self.z
        if lon is None or lat is None:
            raise ValueError("position requires lon/lat (or x/y compatibility fields)")
        if alt_m is None:
            alt_m = 0.0
        self.lon = float(lon)
        self.lat = float(lat)
        self.altM = float(alt_m)
        self.x = float(self.lon)
        self.y = float(self.lat)
        self.z = float(self.altM)
        return self


def _dump_waypoint_payload_model(w: WaypointModel) -> Dict[str, Any]:
    return w.model_dump(by_alias=True, exclude_none=True)


class PlanRoutePayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    route_id: str = "route-1"
    waypoints: List[WaypointModel] = Field(default_factory=lambda: [WaypointModel(**wp) for wp in DEFAULT_ROUTE])


class FleetCreateUavPayload(BaseModel):
    uav_id: Optional[str] = None
    user_id: Optional[str] = None
    operator_license_id: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    altM: Optional[float] = None

    @model_validator(mode="after")
    def _normalize_geo(self) -> "FleetCreateUavPayload":
        lon = self.lon if self.lon is not None else self.x
        lat = self.lat if self.lat is not None else self.y
        alt_m = self.altM if self.altM is not None else self.z
        self.lon = float(lon if lon is not None else OTANIEMI_CENTER_LON)
        self.lat = float(lat if lat is not None else OTANIEMI_CENTER_LAT)
        self.altM = float(alt_m if alt_m is not None else 40.0)
        self.x = float(self.lon)
        self.y = float(self.lat)
        self.z = float(self.altM)
        return self


class UavDemoSeedPayload(BaseModel):
    user_id: Optional[str] = "user-1"
    count: int = 3


class ResetRoutePayload(BaseModel):
    uav_id: str = "uav-1"
    route_id: Optional[str] = None


class FleetDeleteUavPayload(BaseModel):
    uav_id: str = "uav-1"


class UavRegistryAssignPayload(BaseModel):
    user_id: str = "user-1"
    uav_id: str = "uav-1"
    operator_license_id: Optional[str] = None


class UavRegistryProfilePayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    uav_name: Optional[str] = None
    uav_serial_number: Optional[str] = None
    uav_registration_number: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    platform_type: Optional[Literal["multirotor", "fixed_wing", "vtol", "hybrid"]] = None
    uav_category: Optional[Literal["recreational", "commercial", "industrial", "public_safety", "research", "delivery"]] = None
    uav_size_class: Optional[Literal["small", "middle", "heavy"]] = None
    max_takeoff_weight_kg: Optional[float] = None
    empty_weight_kg: Optional[float] = None
    payload_capacity_kg: Optional[float] = None
    max_speed_mps_capability: Optional[float] = None
    max_altitude_m: Optional[float] = None
    max_flight_time_min: Optional[float] = None
    battery_type: Optional[str] = None
    battery_capacity_mah: Optional[float] = None
    remote_id_enabled: Optional[bool] = None
    remote_id: Optional[str] = None
    c2_link_type: Optional[str] = None
    launch_site_id: Optional[str] = None
    landing_site_id: Optional[str] = None
    contingency_action: Optional[str] = None
    weather_min_visibility_km: Optional[float] = None
    weather_max_wind_mps: Optional[float] = None
    home_base_id: Optional[str] = None
    home_x: Optional[float] = None
    home_y: Optional[float] = None
    home_z: Optional[float] = None
    status: Optional[Literal["active", "maintenance", "grounded", "retired"]] = None
    firmware_version: Optional[str] = None
    airworthiness_status: Optional[Literal["airworthy", "inspection_due", "maintenance_required", "grounded"]] = None
    last_maintenance_at: Optional[str] = None
    next_maintenance_due_at: Optional[str] = None
    owner_org_id: Optional[str] = None
    owner_name: Optional[str] = None
    notes: Optional[str] = None


class UavMissionDefaultsPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    route_id: Optional[str] = None
    airspace_segment: Optional[str] = None
    requested_speed_mps: Optional[float] = None
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None
    hold_reason: Optional[str] = None
    mission_priority: Optional[str] = None
    operation_type: Optional[str] = None
    c2_link_type: Optional[Literal["rf", "lte", "5g", "satellite", "hybrid"]] = None


class UavRegistryUserQueryPayload(BaseModel):
    user_id: str = "user-1"


class ApprovalPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"
    requested_speed_mps: float = 12.0
    dss_conflict_policy: str = "reject"  # reject | negotiate | conditional_approve
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class WeatherPayload(BaseModel):
    airspace_segment: str = "sector-A3"
    wind_mps: float = 8.0
    visibility_km: float = 10.0
    precip_mmph: float = 0.0
    storm_alert: bool = False


class StepPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    ticks: int = 1


class HoldPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    reason: str = "operator_request"


class MissionActionPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    action: str = "photo"  # photo | measure | temperature | inspect | hover
    note: Optional[str] = None


class ReplanPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    user_request: str = "avoid nfz on north side"
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointModel]] = None
    optimization_profile: str = "balanced"
    operator_license_id: Optional[str] = None
    auto_utm_verify: bool = True
    route_category: str = "agent_replanned"
    replan_context: str = "general"


class PathRecordDeletePayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    category: str = "user_planned"


class UavAgentChatPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    prompt: str = "Optimize path considering NFZ and network coverage"
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointModel]] = None
    optimization_profile: str = "balanced"
    operator_license_id: str = "op-001"
    network_mode: Optional[str] = None  # coverage | qos | power
    auto_verify: bool = True
    auto_network_optimize: bool = True


class LicensePayload(BaseModel):
    operator_license_id: str
    license_class: str = "VLOS"
    uav_size_class: str = "middle"
    expires_at: str = "2099-01-01T00:00:00Z"
    active: bool = True


class NoFlyZonePayload(BaseModel):
    zone_id: Optional[str] = None
    cx: Optional[float] = None
    cy: Optional[float] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    shape: str = "circle"
    radius_m: float = 30.0
    z_min: float = 0.0
    z_max: float = 120.0
    reason: str = "operator_defined"

    @model_validator(mode="after")
    def _normalize_geo(self) -> "NoFlyZonePayload":
        lon = self.lon if self.lon is not None else self.cx
        lat = self.lat if self.lat is not None else self.cy
        if lon is None or lat is None:
            raise ValueError("no-fly-zone requires lon/lat (or cx/cy compatibility fields)")
        self.lon = float(lon)
        self.lat = float(lat)
        self.cx = float(self.lon)
        self.cy = float(self.lat)
        self.shape = "circle" if str(self.shape or "circle").strip().lower() == "circle" else "box"
        return self


class CorridorPayload(BaseModel):
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"


class RouteCheckPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    requested_speed_mps: float = 12.0
    operator_license_id: Optional[str] = None
    route_id: Optional[str] = None


class TimeWindowCheckPayload(BaseModel):
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None
    operator_license_id: Optional[str] = None


class LicenseCheckPayload(BaseModel):
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"


class VerifyFromUavPayload(BaseModel):
    user_id: Optional[str] = None
    uav_id: str = "uav-1"
    airspace_segment: str = "sector-A3"
    operator_license_id: str = "op-001"
    required_license_class: str = "VLOS"
    requested_speed_mps: float = 12.0
    planned_start_at: Optional[str] = None
    planned_end_at: Optional[str] = None


class NetworkStateQuery(BaseModel):
    airspace_segment: str = "sector-A3"
    selected_uav_id: Optional[str] = None


class NetworkTickPayload(BaseModel):
    steps: int = 1


class NetworkOptimizePayload(BaseModel):
    mode: str = "coverage"  # coverage | power | qos
    coverage_target_pct: float = 96.0
    max_tx_cap_dbm: float = 41.0
    qos_priority_weight: float = 68.0


class NetworkBaseStationUpdatePayload(BaseModel):
    bs_id: str
    txPowerDbm: Optional[float] = None
    tiltDeg: Optional[float] = None
    loadPct: Optional[float] = None
    status: Optional[str] = None


class UavLiveTelemetryPayload(BaseModel):
    uav_id: str = "uav-1"
    route_id: Optional[str] = None
    waypoints: Optional[List[WaypointModel]] = None
    position: Optional[PositionModel] = None
    waypoint_index: Optional[int] = None
    velocity_mps: Optional[float] = None
    battery_pct: Optional[float] = None
    flight_phase: Optional[str] = None
    armed: Optional[bool] = None
    active: Optional[bool] = None
    source: str = "live_uav_feed"
    source_ref: Optional[str] = None
    observed_at: Optional[str] = None


class UavControlBridgeCommandPayload(BaseModel):
    uav_id: str = "uav-1"
    operation: Literal["launch", "step", "hold", "resume", "rth", "land"] = "launch"
    params: Dict[str, Any] = Field(default_factory=dict)
    requested_at: Optional[str] = None
    command_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    caller: Optional[str] = None


class UavControlBridgeTelemetryPayload(BaseModel):
    route_id: Optional[str] = None
    position: Optional[PositionModel] = None
    waypoint_index: Optional[int] = None
    velocity_mps: Optional[float] = None
    battery_pct: Optional[float] = None
    flight_phase: Optional[str] = None
    armed: Optional[bool] = None
    active: Optional[bool] = None
    source: str = "mavlink_bridge_stub"


class UavControlBridgeResponsePayload(BaseModel):
    status: Literal["success", "error"] = "success"
    adapter: Dict[str, Any] = Field(default_factory=dict)
    command: Dict[str, Any] = Field(default_factory=dict)
    telemetry: Optional[UavControlBridgeTelemetryPayload] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    details: Optional[str] = None



__all__ = [
    "ApprovalPayload",
    "CorridorPayload",
    "FleetCreateUavPayload",
    "FleetDeleteUavPayload",
    "HoldPayload",
    "LicenseCheckPayload",
    "LicensePayload",
    "NetworkBaseStationUpdatePayload",
    "NetworkOptimizePayload",
    "NetworkStateQuery",
    "NetworkTickPayload",
    "NoFlyZonePayload",
    "PathRecordDeletePayload",
    "PlanRoutePayload",
    "PositionModel",
    "ReplanPayload",
    "ResetRoutePayload",
    "RouteCheckPayload",
    "StepPayload",
    "TimeWindowCheckPayload",
    "UavAgentChatPayload",
    "UavControlBridgeCommandPayload",
    "UavControlBridgeResponsePayload",
    "UavControlBridgeTelemetryPayload",
    "UavLiveTelemetryPayload",
    "UavRegistryAssignPayload",
    "UavRegistryProfilePayload",
    "UavMissionDefaultsPayload",
    "UavRegistryUserQueryPayload",
    "VerifyFromUavPayload",
    "WaypointModel",
    "WeatherPayload",
    "_dump_waypoint_payload_model",
]
