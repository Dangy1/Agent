import unittest

from mission_supervisor_agent.planner import build_plan, list_skills, match_skill
from mission_supervisor_agent.skill_catalog import render_skill_plan


class MissionSupervisorSkillCatalogTests(unittest.TestCase):
    def test_list_skills_has_core_entries(self) -> None:
        skills = list_skills()
        ids = {str(s.get("skill_id")) for s in skills}
        self.assertIn("uav_utm_standard_mission", ids)
        self.assertIn("cross_domain_network_assured", ids)
        self.assertIn("dss_conflict_and_subscription", ids)
        self.assertIn("uss_publication_and_watch", ids)

    def test_match_skill_dss(self) -> None:
        matched = match_skill("run dss conflict and subscription checks")
        self.assertIsInstance(matched, dict)
        self.assertEqual(str((matched or {}).get("skill_id")), "dss_conflict_and_subscription")

    def test_render_skill_plan_replaces_placeholders(self) -> None:
        out = render_skill_plan(
            "uav_utm_standard_mission",
            {"uav_id": "uav-9", "route_id": "route-9", "airspace_segment": "sector-A9"},
        )
        self.assertTrue(len(out) > 0)
        self.assertEqual(out[0]["params"]["uav_id"], "uav-9")
        self.assertIn("uav:uav-9:flight_control", out[0]["resource_keys"])

    def test_build_plan_uses_skill_template_when_skill_selected(self) -> None:
        state = {
            "intent": {"domain": "unknown", "skill_id": "dss_conflict_and_subscription"},
            "uav_state_snapshot": {"uav_id": "uav-1", "route_id": "route-1"},
            "utm_state_snapshot": {"airspace_segment": "sector-A3"},
            "network_state_snapshot": {"networkKpis": {"coverageScorePct": 95.0, "avgLatencyMs": 20.0, "highInterferenceRiskCount": 0}},
            "mission_state_snapshot": {"warnings": []},
        }
        plan = build_plan(state)
        ops = [f"{s.get('domain')}.{s.get('op')}" for s in plan]
        self.assertEqual(
            ops,
            [
                "dss.query_operational_intents",
                "dss.query_subscriptions",
                "dss.query_notifications",
                "dss.run_local_conformance",
            ],
        )


if __name__ == "__main__":
    unittest.main()
