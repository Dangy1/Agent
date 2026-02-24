import unittest

from mission_supervisor_agent.planner import build_plan, derive_mission_phase


def _ops(plan: list[dict]) -> list[str]:
    return [f"{s.get('domain')}.{s.get('op')}" for s in plan]


class MissionSupervisorPlannerTests(unittest.TestCase):
    def test_cross_domain_preflight_plan(self) -> None:
        state = {
            "intent": {"domain": "cross_domain", "constraints": {"latency_ms_max": 20}},
            "uav_state_snapshot": {
                "uav_id": "uav-1",
                "route_id": "mission-route-1",
                "flight_phase": "PLANNED",
                "battery_pct": 95,
                "active": False,
                "armed": False,
            },
            "utm_state_snapshot": {
                "airspace_segment": "sector-A3",
                "weather_check": {"ok": True},
                "no_fly_zone_check": {"ok": True},
                "regulation_check": {"ok": True},
                "license_check": {"ok": True},
            },
            "network_state_snapshot": {"networkKpis": {"coverageScorePct": 94, "avgLatencyMs": 18, "highInterferenceRiskCount": 0}},
            "mission_state_snapshot": {"warnings": []},
            "mission_phase": "preflight",
        }
        self.assertEqual(derive_mission_phase(state), "preflight")
        plan = build_plan(state)
        ops = _ops(plan)
        self.assertEqual(
            ops,
            [
                "uav.plan_route",
                "uav.submit_route_geofence",
                "utm.verify_flight_plan",
                "network.health",
                "network.slice_apply_profile",
                "network.kpm_monitor",
            ],
        )

    def test_cross_domain_mitigation_plan(self) -> None:
        state = {
            "intent": {"domain": "cross_domain", "constraints": {"latency_ms_max": 20}},
            "uav_state_snapshot": {
                "uav_id": "uav-1",
                "route_id": "mission-route-1",
                "flight_phase": "MISSION",
                "battery_pct": 12,
                "active": True,
                "armed": True,
            },
            "utm_state_snapshot": {
                "airspace_segment": "sector-A3",
                "weather_check": {"ok": True},
                "no_fly_zone_check": {"ok": True},
                "regulation_check": {"ok": True},
                "license_check": {"ok": True},
            },
            "network_state_snapshot": {"networkKpis": {"coverageScorePct": 82, "avgLatencyMs": 41, "highInterferenceRiskCount": 1}},
            "mission_state_snapshot": {"warnings": ["uav_low_battery", "network_latency_high", "network_interference_risk"]},
            "mission_phase": "mitigation",
        }
        self.assertEqual(derive_mission_phase(state), "mitigation")
        plan = build_plan(state)
        ops = _ops(plan)
        self.assertEqual(
            ops,
            [
                "network.health",
                "network.kpm_monitor",
                "network.slice_apply_profile",
                "uav.hold",
                "uav.status",
                "uav.rth",
            ],
        )

    def test_uav_closeout_plan(self) -> None:
        state = {
            "intent": {"domain": "uav_mission"},
            "uav_state_snapshot": {
                "uav_id": "uav-1",
                "route_id": "uav-mission-1",
                "flight_phase": "ARRIVAL",
                "battery_pct": 40,
                "active": False,
                "armed": True,
            },
            "utm_state_snapshot": {
                "airspace_segment": "sector-A3",
                "weather_check": {"ok": True},
                "no_fly_zone_check": {"ok": True},
                "regulation_check": {"ok": True},
                "license_check": {"ok": True},
            },
            "network_state_snapshot": {"networkKpis": {"coverageScorePct": 90, "avgLatencyMs": 20, "highInterferenceRiskCount": 0}},
            "mission_state_snapshot": {"warnings": []},
        }
        self.assertEqual(derive_mission_phase(state), "closeout")
        plan = build_plan(state)
        self.assertEqual(_ops(plan), ["uav.land", "uav.status"])


if __name__ == "__main__":
    unittest.main()

