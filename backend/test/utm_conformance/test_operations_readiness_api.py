import unittest

try:
    from utm_agent.api import (
        ConformanceRunPayload,
        OperationsReadinessPayload,
        get_operations_playbooks,
        post_operations_readiness_evaluate,
        run_local_conformance,
    )
except Exception as exc:  # pragma: no cover
    get_operations_playbooks = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class OperationsReadinessApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if get_operations_playbooks is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_playbook_index_and_readiness_evaluation(self) -> None:
        run = run_local_conformance(ConformanceRunPayload(reset_before_run=True))  # type: ignore[misc]
        self.assertEqual(run.get("status"), "success")

        playbooks = get_operations_playbooks("us_faa_ntap")  # type: ignore[misc]
        self.assertEqual(playbooks.get("status"), "success")
        idx = ((playbooks.get("result") or {}).get("playbook_index") if isinstance(playbooks.get("result"), dict) else {})
        self.assertGreater(int(idx.get("required_count", 0) or 0), 0)

        readiness = post_operations_readiness_evaluate(  # type: ignore[misc]
            OperationsReadinessPayload(
                jurisdiction_profile="us_faa_ntap",
                observed_metrics={
                    "availability_slo_pct": 99.95,
                    "decision_latency_ms_p95": 350,
                    "recovery_time_objective_min": 30,
                },
            )
        )
        self.assertEqual(readiness.get("status"), "success")
        result = readiness.get("result") if isinstance(readiness.get("result"), dict) else {}
        self.assertTrue(bool(result.get("operations_ready")))


if __name__ == "__main__":
    unittest.main()
