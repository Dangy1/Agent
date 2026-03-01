import os
import unittest

from agent_db import AgentDB
from utm_agent.dss_gateway import gateway_upsert_operational_intent


class DSSGatewayFailoverTests(unittest.TestCase):
    def _set_env(self, **values: str):
        prev = {k: os.environ.get(k) for k in values.keys()}
        for k, v in values.items():
            os.environ[k] = v
        return prev

    def _restore_env(self, prev: dict):
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_external_mode_blocks_without_conformance(self) -> None:
        db = AgentDB("utm_gateway_test_block")
        db.set_state("dss_operational_intents", {})
        db.set_state("dss_conformance_last", {"passed": False, "generated_at": "2026-02-27T00:00:00Z"})
        prev = self._set_env(
            UTM_DSS_ADAPTER_MODE="external",
            UTM_DSS_EXTERNAL_BASE_URL="http://127.0.0.1:1",
            UTM_DSS_FAILOVER_POLICY="block",
            UTM_DSS_REQUIRE_LOCAL_CONFORMANCE="true",
        )
        try:
            out = gateway_upsert_operational_intent(
                db,
                {
                    "intent_id": "gw-oi-1",
                    "manager_uss_id": "uss-local",
                    "state": "accepted",
                    "priority": "normal",
                    "conflict_policy": "reject",
                    "volume4d": {"x": [0, 10], "y": [0, 10], "z": [0, 50], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
                },
            )
            self.assertEqual(str(out.get("status")), "error")
            self.assertEqual(str(out.get("error")), "external_dss_unavailable")
        finally:
            self._restore_env(prev)

    def test_external_mode_degraded_local_fallback(self) -> None:
        db = AgentDB("utm_gateway_test_degraded")
        db.set_state("dss_operational_intents", {})
        db.set_state("dss_conformance_last", {"passed": True, "generated_at": "2026-02-27T12:00:00Z"})
        prev = self._set_env(
            UTM_DSS_ADAPTER_MODE="external",
            UTM_DSS_EXTERNAL_BASE_URL="http://127.0.0.1:1",
            UTM_DSS_FAILOVER_POLICY="degraded_local",
            UTM_DSS_REQUIRE_LOCAL_CONFORMANCE="true",
            UTM_DSS_CONFORMANCE_MAX_AGE_MIN="100000",
        )
        try:
            out = gateway_upsert_operational_intent(
                db,
                {
                    "intent_id": "gw-oi-2",
                    "manager_uss_id": "uss-local",
                    "state": "accepted",
                    "priority": "normal",
                    "conflict_policy": "reject",
                    "volume4d": {"x": [20, 30], "y": [20, 30], "z": [0, 50], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
                },
            )
            self.assertEqual(str(out.get("status")), "success")
            self.assertTrue(bool(out.get("degraded")))
            intent = ((out.get("result") or {}).get("intent") if isinstance(out.get("result"), dict) else {})
            self.assertEqual(str(intent.get("intent_id")), "gw-oi-2")
        finally:
            self._restore_env(prev)


if __name__ == "__main__":
    unittest.main()
