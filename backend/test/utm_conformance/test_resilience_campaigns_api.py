import unittest

try:
    from utm_agent.api import (
        ResilienceCampaignRunPayload,
        ResilienceCampaignStartPayload,
        get_resilience_campaign_summary,
        get_resilience_campaigns,
        post_resilience_campaign_run,
        post_resilience_campaign_start,
    )
except Exception as exc:  # pragma: no cover
    post_resilience_campaign_start = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class ResilienceCampaignApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if post_resilience_campaign_start is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_resilience_campaign_lifecycle(self) -> None:
        start = post_resilience_campaign_start(  # type: ignore[misc]
            ResilienceCampaignStartPayload(
                name="weekly-resilience",
                release_id="rel-res-1",
                cadence_days=7,
                created_by="qa",
            )
        )
        self.assertEqual(start.get("status"), "success")
        campaign_id = str((start.get("result") or {}).get("campaign_id") or "")
        self.assertTrue(campaign_id.startswith("res-"))

        run = post_resilience_campaign_run(  # type: ignore[misc]
            campaign_id,
            ResilienceCampaignRunPayload(executed_by="qa", fault_profile="dss_outage"),
        )
        self.assertEqual(run.get("status"), "success")

        summary = get_resilience_campaign_summary(campaign_id)  # type: ignore[misc]
        self.assertEqual(summary.get("status"), "success")
        result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
        self.assertGreaterEqual(int(result.get("total_runs", 0) or 0), 1)

        listed = get_resilience_campaigns(release_id="rel-res-1")  # type: ignore[misc]
        self.assertEqual(listed.get("status"), "success")
        self.assertTrue(
            any(
                isinstance(x, dict) and str(x.get("campaign_id", "")) == campaign_id
                for x in ((listed.get("result") or {}).get("items") or [])
            )
        )


if __name__ == "__main__":
    unittest.main()
