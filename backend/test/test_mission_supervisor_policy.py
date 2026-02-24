import unittest

from mission_supervisor_agent.command_types import classify_command_operation_type
from mission_supervisor_agent.policy import build_proposed_actions, validate_policy


def _with_proposed_actions(state: dict) -> dict:
    s = dict(state)
    s["proposed_actions"] = build_proposed_actions(s)
    return s


class CommandTypesTests(unittest.TestCase):
    def test_classify_observe_and_actuate(self) -> None:
        self.assertEqual(classify_command_operation_type({"domain": "network", "op": "health"}), "observe")
        self.assertEqual(classify_command_operation_type({"domain": "uav", "op": "launch"}), "actuate")
        self.assertEqual(classify_command_operation_type({"domain": "x", "op": "y"}), "unknown")


class PolicyGuardrailTests(unittest.TestCase):
    def test_observe_health_allowed_in_closeout(self) -> None:
        state = _with_proposed_actions(
            {
                "mission_phase": "closeout",
                "risk_level": "high",
                "current_step": 0,
                "plan": [{"step_id": "health", "domain": "network", "op": "health", "params": {}}],
                "uav_state_snapshot": {"flight_phase": "ARRIVAL"},
                "network_state_snapshot": {"networkKpis": {"avgLatencyMs": 99}},
                "approvals": [],
            }
        )
        self.assertEqual(validate_policy(state), [])

    def test_observe_verify_requires_waypoints(self) -> None:
        state = _with_proposed_actions(
            {
                "mission_phase": "preflight",
                "risk_level": "high",
                "current_step": 0,
                "plan": [{"step_id": "verify", "domain": "utm", "op": "verify_flight_plan", "params": {}}],
                "uav_state_snapshot": {"waypoints_total": 0, "waypoints": []},
                "approvals": [],
            }
        )
        notes = validate_policy(state)
        self.assertIn("utm_verify_blocked_missing_waypoints", notes)

    def test_actuate_slice_apply_blocked_without_phase(self) -> None:
        state = _with_proposed_actions(
            {
                "mission_phase": "unknown",
                "risk_level": "medium",
                "current_step": 0,
                "plan": [
                    {
                        "step_id": "slice",
                        "domain": "network",
                        "op": "slice_apply_profile",
                        "params": {"profile": "static"},
                    }
                ],
                "network_state_snapshot": {"networkKpis": {"avgLatencyMs": 10}},
            }
        )
        notes = validate_policy(state)
        self.assertIn("actuation_blocked_missing_mission_phase", notes)
        self.assertTrue(any(n.startswith("action_blocked_phase_mismatch:unknown:network.slice_apply_profile") for n in notes))

    def test_actuate_launch_blocked_on_network_interference_warning(self) -> None:
        state = _with_proposed_actions(
            {
                "mission_phase": "launch",
                "risk_level": "high",
                "current_step": 0,
                "plan": [{"step_id": "launch", "domain": "uav", "op": "launch", "params": {"uav_id": "uav-1"}}],
                "uav_state_snapshot": {"flight_phase": "PLANNED", "battery_pct": 80},
                "utm_state_snapshot": {
                    "weather_check": {"ok": True},
                    "no_fly_zone_check": {"ok": True},
                    "regulation_check": {"ok": True},
                    "license_check": {"ok": True},
                },
                "mission_state_snapshot": {"warnings": ["network_interference_risk"]},
                "approvals": [
                    {
                        "issuer": "utm",
                        "approved": True,
                        "signature_verified": True,
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                ],
            }
        )
        notes = validate_policy(state)
        self.assertIn("uav_launch_blocked_network_interference_risk", notes)

    def test_emergency_hold_allowed_without_approvals(self) -> None:
        state = _with_proposed_actions(
            {
                "mission_phase": "mitigation",
                "risk_level": "high",
                "current_step": 0,
                "plan": [{"step_id": "hold", "domain": "uav", "op": "hold", "params": {"uav_id": "uav-1"}}],
                "uav_state_snapshot": {"flight_phase": "MISSION", "battery_pct": 12, "active": True},
                "mission_state_snapshot": {"warnings": ["uav_low_battery"]},
                "approvals": [],
            }
        )
        self.assertEqual(validate_policy(state), [])


if __name__ == "__main__":
    unittest.main()

