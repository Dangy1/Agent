import unittest

try:
    from utm_agent.api import get_compliance_export
except Exception as exc:  # pragma: no cover - depends on optional test deps
    get_compliance_export = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class ComplianceExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if get_compliance_export is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_compliance_export_contains_rtm_artifact(self) -> None:
        response = get_compliance_export(limit_actions=20, include_action_payloads=False, include_rtm_text=False)  # type: ignore[misc]
        self.assertEqual(response.get("status"), "success")
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        rtm_artifact = next((a for a in artifacts if isinstance(a, dict) and a.get("artifact_id") == "rtm"), None)
        self.assertIsNotNone(rtm_artifact)
        self.assertTrue(bool(rtm_artifact.get("present")))

        rtm = result.get("rtm") if isinstance(result.get("rtm"), dict) else {}
        summary = rtm.get("summary") if isinstance(rtm.get("summary"), dict) else {}
        self.assertGreater(int(summary.get("requirement_count", 0) or 0), 0)
        self.assertIn("coverage_pct", summary)

    def test_compliance_export_respects_action_limit(self) -> None:
        response = get_compliance_export(limit_actions=3, include_action_payloads=False, include_rtm_text=False)  # type: ignore[misc]
        self.assertEqual(response.get("status"), "success")
        result = response.get("result") if isinstance(response.get("result"), dict) else {}
        evidence_index = result.get("evidence_index") if isinstance(result.get("evidence_index"), list) else []
        self.assertLessEqual(len(evidence_index), 3)


if __name__ == "__main__":
    unittest.main()
