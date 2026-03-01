#!/usr/bin/env python3
"""Replay: submit with DSS conflict -> auto detour -> retry -> launch gate check."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import uav_agent.api_routes_uav as api_routes_uav
import uav_agent.api_shared as api_shared
from uav_agent.api_models import ApprovalPayload, PlanRoutePayload, ReplanPayload
from uav_agent.api_shared import SIM, UTM_DB_MIRROR, UTM_SERVICE
from utm_agent.dss_gateway import gateway_upsert_operational_intent


def _now_window() -> tuple[str, str]:
    start = datetime.now(timezone.utc) + timedelta(minutes=6)
    end = start + timedelta(minutes=20)
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def _require_ok(body: Dict[str, Any], *, label: str) -> Dict[str, Any]:
    status = str(body.get("status", "")).strip().lower()
    if status not in {"success", "warning", "ok"}:
        raise AssertionError(f"{label}: expected status=success/warning/ok, got {body}")
    return body


def main() -> None:
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    uav_id = f"uav-{uuid.uuid4().hex[:8]}"
    route_id = f"{uav_id}-route-1"
    airspace = "sector-A3"

    old_intents = UTM_DB_MIRROR.get_state("dss_operational_intents")
    old_nfz = [dict(z) for z in UTM_SERVICE.no_fly_zones]
    old_approvals = dict(UTM_SERVICE.approvals)
    old_route_log = api_routes_uav._log_uav_action
    old_shared_log = api_shared._log_uav_action
    old_refresh = api_routes_uav._refresh_utm_mirror_from_real_service
    tool_overrides = [
        api_routes_uav.uav_plan_route,
        api_routes_uav.uav_submit_route_to_utm_geofence_check,
        api_routes_uav.uav_request_utm_approval,
        api_routes_uav.uav_replan_route_via_utm_nfz,
        api_routes_uav.uav_launch,
    ]
    old_invoke = [tool.invoke for tool in tool_overrides]

    try:
        api_routes_uav._log_uav_action = lambda *args, **kwargs: {"agent": "uav", "revision": 0, "updated_at": "test"}
        api_shared._log_uav_action = lambda *args, **kwargs: {"agent": "uav", "revision": 0, "updated_at": "test"}
        api_routes_uav._refresh_utm_mirror_from_real_service = lambda **kwargs: {"status": "skipped", "reason": "test"}
        for tool in tool_overrides:
            object.__setattr__(tool, "invoke", (lambda payload, _tool=tool: _tool.func(**dict(payload or {}))))
        UTM_DB_MIRROR.set_state("dss_operational_intents", {})
        UTM_SERVICE.no_fly_zones = []

        waypoints = [
            {"x": 100.0, "y": 40.0, "z": 40.0, "action": "transit"},
            {"x": 150.0, "y": 100.0, "z": 40.0, "action": "transit"},
            {"x": 200.0, "y": 40.0, "z": 40.0, "action": "photo"},
        ]

        print("Step 1/5: plan route")
        _require_ok(
            api_routes_uav.post_plan_route(
                PlanRoutePayload(
                    user_id=user_id,
                    uav_id=uav_id,
                    route_id=route_id,
                    waypoints=waypoints,
                )
            ),
            label="plan",
        )
        SIM.ingest_live_state(
            uav_id,
            route_id=route_id,
            waypoints=waypoints,
            battery_pct=92.0,
            source="simulated",
            source_meta={"scenario": "dss_conflict_detour_retry_launch"},
        )

        start_at, end_at = _now_window()
        peer_out = gateway_upsert_operational_intent(
            UTM_DB_MIRROR,
            {
                "intent_id": f"peer:{uav_id}:blocking",
                "manager_uss_id": "uss-local-peer-scenario",
                "state": "accepted",
                "priority": "high",
                "conflict_policy": "reject",
                "ovn": None,
                "uss_base_url": "",
                "volume4d": {
                    "x": [145.0, 155.0],
                    "y": [95.0, 105.0],
                    "z": [0.0, 60.0],
                    "time_start": start_at,
                    "time_end": end_at,
                },
                "metadata": {"scenario": "blocking_peer_intent"},
            },
        )
        if str(peer_out.get("status")) != "success":
            raise AssertionError(f"Step 2 setup failed to create peer DSS intent: {peer_out}")

        submit_payload = {
            "user_id": user_id,
            "uav_id": uav_id,
            "airspace_segment": airspace,
            "operator_license_id": "op-001",
            "required_license_class": "VLOS",
            "requested_speed_mps": 12.0,
            "planned_start_at": start_at,
            "planned_end_at": end_at,
        }

        print("Step 2/5: submit mission (expect DSS conflict block)")
        submit_1 = _require_ok(
            api_routes_uav.post_utm_submit_mission(ApprovalPayload(**submit_payload)),
            label="submit_1",
        )
        agg_1 = submit_1.get("result") if isinstance(submit_1.get("result"), dict) else {}
        approval_1 = agg_1.get("approval_request") if isinstance(agg_1, dict) and isinstance(agg_1.get("approval_request"), dict) else {}
        approval_1_result = approval_1.get("result") if isinstance(approval_1, dict) and isinstance(approval_1.get("result"), dict) else {}
        if bool(agg_1.get("approved")):
            raise AssertionError(f"Expected first submit to be not approved, got {agg_1}")
        if str(approval_1_result.get("error")) != "dss_strategic_conflict":
            raise AssertionError(f"Expected dss_strategic_conflict, got {approval_1_result}")

        print("Step 3/5: apply auto detour replan")
        UTM_SERVICE.no_fly_zones = [
            {
                "zone_id": "scenario-detour-zone",
                "cx": 150.0,
                "cy": 100.0,
                "radius_m": 8.0,
                "z_min": 0.0,
                "z_max": 80.0,
                "reason": "scenario_detour",
            }
        ]
        replan = _require_ok(
            api_routes_uav.post_replan_via_utm_nfz(
                ReplanPayload(
                    user_id=user_id,
                    uav_id=uav_id,
                    airspace_segment=airspace,
                    operator_license_id="op-001",
                    optimization_profile="balanced",
                    auto_utm_verify=True,
                    user_request="Resolve strategic conflict with south-side detour.",
                )
            ),
            label="replan",
        )
        if str(replan.get("status")) != "success":
            raise AssertionError(f"Expected replan success, got {replan}")

        print("Step 4/5: retry mission submit after detour")
        UTM_SERVICE.no_fly_zones = []
        submit_2 = _require_ok(
            api_routes_uav.post_utm_submit_mission(ApprovalPayload(**submit_payload)),
            label="submit_2",
        )
        agg_2 = submit_2.get("result") if isinstance(submit_2.get("result"), dict) else {}
        if bool(agg_2.get("approved")) is not True:
            raise AssertionError(f"Expected retry submit approved=True, got {agg_2}")

        print("Step 5/5: launch gate check + launch")
        state_body = _require_ok(api_routes_uav.get_sim_state(uav_id=uav_id, user_id=user_id), label="state")
        flight_gate = state_body.get("flight_gate") if isinstance(state_body, dict) and isinstance(state_body.get("flight_gate"), dict) else {}
        if flight_gate.get("launch_ready") is not True:
            raise AssertionError(f"Expected launch_ready=True after retry, got {flight_gate}")

        launch_body = _require_ok(api_routes_uav.post_launch(uav_id=uav_id, user_id=user_id), label="launch")
        if str((launch_body.get("result") or {}).get("flight_phase", "")).upper() not in {"TAKEOFF", "MISSION"}:
            raise AssertionError(f"Expected launch to transition flight phase, got {launch_body}")
        print("Scenario replay passed.")
    finally:
        if isinstance(old_intents, dict):
            UTM_DB_MIRROR.set_state("dss_operational_intents", old_intents)
        else:
            UTM_DB_MIRROR.delete_state("dss_operational_intents")
        UTM_SERVICE.no_fly_zones = [dict(z) for z in old_nfz]
        UTM_SERVICE.approvals = dict(old_approvals)
        api_routes_uav._log_uav_action = old_route_log
        api_shared._log_uav_action = old_shared_log
        api_routes_uav._refresh_utm_mirror_from_real_service = old_refresh
        for tool, invoke_fn in zip(tool_overrides, old_invoke):
            object.__setattr__(tool, "invoke", invoke_fn)


if __name__ == "__main__":
    main()
