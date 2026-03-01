import unittest
from pathlib import Path

from utm_agent.cert_pack import (
    build_certification_pack,
    list_available_profiles,
    load_jurisdiction_profile,
    parse_rtm_requirements,
)


class CertificationPackModuleTests(unittest.TestCase):
    def test_load_profiles_and_parse_rtm(self) -> None:
        root = Path(__file__).resolve().parents[3]
        profiles_dir = root / "profiles"
        ids = list_available_profiles(profiles_dir)
        self.assertIn("us_faa_ntap", ids)
        profile = load_jurisdiction_profile("us_faa_ntap", profiles_dir)
        self.assertEqual(profile.get("profile_id"), "us_faa_ntap")

        rtm_path = root / "docs" / "compliance" / "rtm.yaml"
        raw = rtm_path.read_text(encoding="utf-8")
        reqs = parse_rtm_requirements(raw)
        self.assertGreater(len(reqs), 0)
        self.assertTrue(any(str(r.get("id", "")).startswith("UTM-DSS-") for r in reqs))

    def test_build_pack_generates_claim_evidence_index(self) -> None:
        profile = {
            "profile_id": "test_profile",
            "name": "Test Profile",
            "version": "1.0",
            "governance": {"required_approvals": ["safety", "security"]},
            "cyber_controls": [{"control_id": "TC-1", "description": "test", "evidence_ids": ["evi-1"]}],
            "incident_process": {"required_artifacts": ["incident_response_plan"]},
            "continuity": {"availability_slo_pct": 99.9},
            "software_assurance": {"required_artifacts": ["sbom"]},
        }
        requirements = [
            {
                "id": "REQ-1",
                "phase": "MVP",
                "status": "implemented",
                "requirement": "Test requirement",
                "evidence_ids": ["evi-1"],
            },
            {
                "id": "REQ-2",
                "phase": "Pre-Cert",
                "status": "missing",
                "requirement": "Gap requirement",
                "evidence_ids": ["evi-2"],
            },
        ]
        pack = build_certification_pack(
            profile=profile,
            rtm_requirements=requirements,
            conformance={"passed": True},
            evidence_index=[{"evidence_id": "evi-1"}],
            release_id="rel-1",
            candidate_version="1.2.3",
            approvals=[{"role": "safety", "status": "approved"}, {"role": "security", "status": "approved"}],
            notes="unit-test",
        )
        self.assertTrue(str(pack.get("pack_id", "")).startswith("cert-"))
        summary = pack.get("summary") if isinstance(pack.get("summary"), dict) else {}
        self.assertEqual(int(summary.get("total_claims", 0) or 0), 2)
        self.assertEqual(int(summary.get("gap_claims", 0) or 0), 1)
        self.assertFalse(bool(summary.get("release_ready")))
        evidence_index = pack.get("evidence_index") if isinstance(pack.get("evidence_index"), dict) else {}
        self.assertGreater(len(evidence_index.get("claims_to_evidence") or []), 0)


if __name__ == "__main__":
    unittest.main()
