import unittest

from utm_agent.release_governance import create_deviation_record, evaluate_release_gate, resolve_deviation_record


class ReleaseGovernanceModuleTests(unittest.TestCase):
    def test_gate_blocks_when_missing_approvals_or_open_critical_deviation(self) -> None:
        pack = {
            "pack_id": "cert-1",
            "summary": {
                "conformance_passed": True,
                "critical_findings": 1,
                "missing_approvals": ["security"],
                "release_ready": False,
            },
            "governance": {"provided_approvals": [{"role": "safety", "status": "approved"}]},
        }
        dev = create_deviation_record(
            release_id="rel-1",
            category="security",
            severity="critical",
            description="open issue",
            owner="ops",
        )
        out = evaluate_release_gate(
            release_id="rel-1",
            pack=pack,
            required_approvals=["safety", "security"],
            deviations=[dev],
            latest_conformance={"passed": True},
            require_signed_campaign_report=False,
            campaign_report=None,
        )
        self.assertEqual(out.get("decision"), "block")
        reasons = [str(x) for x in (out.get("reasons") or [])]
        self.assertIn("missing_required_approvals", reasons)
        self.assertIn("open_critical_deviations_present", reasons)

    def test_gate_allows_when_checks_pass(self) -> None:
        pack = {
            "pack_id": "cert-2",
            "summary": {
                "conformance_passed": True,
                "critical_findings": 0,
                "missing_approvals": [],
                "release_ready": True,
            },
            "governance": {
                "provided_approvals": [
                    {"role": "safety", "status": "approved"},
                    {"role": "security", "status": "approved"},
                ]
            },
        }
        out = evaluate_release_gate(
            release_id="rel-2",
            pack=pack,
            required_approvals=["safety", "security"],
            deviations=[],
            latest_conformance={"passed": True},
            require_signed_campaign_report=False,
            campaign_report=None,
        )
        self.assertEqual(out.get("decision"), "allow")

    def test_resolve_deviation(self) -> None:
        dev = create_deviation_record(
            release_id="rel-3",
            category="compliance",
            severity="major",
            description="test",
            owner="qa",
        )
        resolved = resolve_deviation_record(dev, status="resolved", resolver="qa-lead", resolution_note="mitigated")
        self.assertEqual(str(resolved.get("status")), "resolved")
        self.assertEqual(str((resolved.get("resolution") or {}).get("resolver")), "qa-lead")


if __name__ == "__main__":
    unittest.main()
