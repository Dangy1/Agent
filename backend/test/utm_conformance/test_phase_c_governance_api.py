import unittest

try:
    from utm_agent.api import (
        CertificationApprovalPayload,
        CertificationPackGeneratePayload,
        DeviationPayload,
        DeviationResolvePayload,
        InteropCampaignRunPayload,
        InteropCampaignSignPayload,
        InteropCampaignStartPayload,
        ReleaseGateEvaluatePayload,
        generate_certification_pack,
        get_campaign_report,
        get_campaigns,
        get_release_deviations,
        post_campaign_report_sign,
        post_campaign_run,
        post_campaign_start,
        post_release_deviation,
        post_release_deviation_resolve,
        post_release_gate_evaluate,
    )
except Exception as exc:  # pragma: no cover
    generate_certification_pack = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class PhaseCGovernanceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if generate_certification_pack is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_release_gate_and_deviation_workflow(self) -> None:
        pack_payload = CertificationPackGeneratePayload(  # type: ignore[misc]
            jurisdiction_profile="us_faa_ntap",
            release_id="rel-phase-c",
            candidate_version="3.0.0",
            approvals=[
                CertificationApprovalPayload(role="safety", approver="a", status="approved"),
                CertificationApprovalPayload(role="security", approver="b", status="approved"),
                CertificationApprovalPayload(role="compliance", approver="c", status="approved"),
                CertificationApprovalPayload(role="release_manager", approver="d", status="approved"),
            ],
        )
        pack_res = generate_certification_pack(pack_payload)  # type: ignore[misc]
        self.assertEqual(pack_res.get("status"), "success")
        pack_id = str((pack_res.get("result") or {}).get("pack_id") or "")
        self.assertTrue(pack_id.startswith("cert-"))

        dev = post_release_deviation(  # type: ignore[misc]
            DeviationPayload(
                release_id="rel-phase-c",
                category="security",
                severity="critical",
                description="temporary exception",
                owner="ops",
            )
        )
        self.assertEqual(dev.get("status"), "success")
        deviation_id = str((dev.get("result") or {}).get("deviation_id") or "")
        self.assertTrue(deviation_id.startswith("dev-"))

        gate_block = post_release_gate_evaluate(  # type: ignore[misc]
            ReleaseGateEvaluatePayload(
                release_id="rel-phase-c",
                pack_id=pack_id,
                required_approvals=["safety", "security", "compliance", "release_manager"],
            )
        )
        self.assertEqual(gate_block.get("status"), "success")
        self.assertEqual(str((gate_block.get("result") or {}).get("decision") or ""), "block")

        resolved = post_release_deviation_resolve(  # type: ignore[misc]
            deviation_id,
            DeviationResolvePayload(status="resolved", resolver="ops-lead", resolution_note="cleared"),
        )
        self.assertEqual(resolved.get("status"), "success")

        gate_allow = post_release_gate_evaluate(  # type: ignore[misc]
            ReleaseGateEvaluatePayload(
                release_id="rel-phase-c",
                pack_id=pack_id,
                required_approvals=["safety", "security", "compliance", "release_manager"],
            )
        )
        self.assertEqual(gate_allow.get("status"), "success")
        self.assertEqual(str((gate_allow.get("result") or {}).get("decision") or ""), "allow")

        listed = get_release_deviations(release_id="rel-phase-c")  # type: ignore[misc]
        self.assertEqual(listed.get("status"), "success")
        self.assertGreaterEqual(int((listed.get("result") or {}).get("count", 0) or 0), 1)

    def test_interop_campaign_workflow_and_report_sign(self) -> None:
        camp = post_campaign_start(  # type: ignore[misc]
            InteropCampaignStartPayload(
                name="phase-c-campaign",
                jurisdiction_profile="us_faa_ntap",
                release_id="rel-phase-c",
                partners=["uss-a", "uss-b"],
                scenarios=["operational_intent_lifecycle", "conflict_resolution"],
                created_by="qa",
            )
        )
        self.assertEqual(camp.get("status"), "success")
        campaign_id = str((camp.get("result") or {}).get("campaign_id") or "")
        self.assertTrue(campaign_id.startswith("camp-"))

        r1 = post_campaign_run(  # type: ignore[misc]
            campaign_id,
            InteropCampaignRunPayload(
                partner_id="uss-a",
                scenario_id="operational_intent_lifecycle",
                status="passed",
                evidence_ids=["evi-a1"],
            ),
        )
        self.assertEqual(r1.get("status"), "success")
        r2 = post_campaign_run(  # type: ignore[misc]
            campaign_id,
            InteropCampaignRunPayload(
                partner_id="uss-b",
                scenario_id="conflict_resolution",
                status="passed",
                evidence_ids=["evi-b1"],
            ),
        )
        self.assertEqual(r2.get("status"), "success")

        signed = post_campaign_report_sign(  # type: ignore[misc]
            campaign_id,
            InteropCampaignSignPayload(
                signed_by="interop-board",
                signature_ref="sig://phase-c",
                decision="accepted",
            ),
        )
        self.assertEqual(signed.get("status"), "success")

        report = get_campaign_report(campaign_id)  # type: ignore[misc]
        self.assertEqual(report.get("status"), "success")
        summary = ((report.get("result") or {}).get("summary") if isinstance((report.get("result") or {}).get("summary"), dict) else {})
        self.assertEqual(int(summary.get("failed_runs", 0) or 0), 0)
        signature = ((report.get("result") or {}).get("signature") if isinstance((report.get("result") or {}).get("signature"), dict) else {})
        self.assertEqual(str(signature.get("status") or ""), "signed")

        campaigns = get_campaigns(release_id="rel-phase-c")  # type: ignore[misc]
        self.assertEqual(campaigns.get("status"), "success")
        self.assertTrue(any(isinstance(x, dict) and x.get("campaign_id") == campaign_id for x in ((campaigns.get("result") or {}).get("items") or [])))


if __name__ == "__main__":
    unittest.main()
