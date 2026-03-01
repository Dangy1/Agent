import unittest

from utm_agent.interoperability_campaigns import (
    append_campaign_run,
    build_campaign_report,
    create_campaign,
    sign_campaign_report,
)


class InteroperabilityCampaignModuleTests(unittest.TestCase):
    def test_campaign_lifecycle_and_report(self) -> None:
        campaign = create_campaign(
            name="multi-uss",
            jurisdiction_profile="us_faa_ntap",
            release_id="rel-1",
            partners=["uss-a", "uss-b"],
            scenarios=["operational_intent_lifecycle", "notification_delivery"],
            created_by="qa",
        )
        campaign = append_campaign_run(
            campaign,
            partner_id="uss-a",
            scenario_id="operational_intent_lifecycle",
            status="passed",
            summary="ok",
            evidence_ids=["e-1", "e-2"],
        )
        campaign = append_campaign_run(
            campaign,
            partner_id="uss-b",
            scenario_id="notification_delivery",
            status="passed",
            summary="ok",
            evidence_ids=["e-3"],
        )
        signed = sign_campaign_report(
            campaign,
            signed_by="interop-board",
            signature_ref="sig://campaign/1",
            decision="accepted",
            note="validated",
        )
        report = build_campaign_report(signed, compliance_export={"status": "success"})
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        self.assertEqual(int(summary.get("total_runs", 0) or 0), 2)
        self.assertEqual(int(summary.get("failed_runs", 0) or 0), 0)
        self.assertTrue(bool(summary.get("independent_review_ready")))
        signature = report.get("signature") if isinstance(report.get("signature"), dict) else {}
        self.assertEqual(str(signature.get("status") or ""), "signed")


if __name__ == "__main__":
    unittest.main()
