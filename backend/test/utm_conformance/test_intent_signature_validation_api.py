import unittest

try:
    from utm_agent.api import (
        OperationalIntentPayload,
        Volume4DPayload,
        _get_security_controls,
        _set_security_controls,
        post_operational_intent,
    )
    from utm_agent.security_controls import register_peer_key, sign_payload
except Exception as exc:  # pragma: no cover
    post_operational_intent = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class IntentSignatureValidationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if post_operational_intent is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_local_intent_is_signed(self) -> None:
        payload = OperationalIntentPayload(  # type: ignore[misc]
            intent_id="sig-local-1",
            manager_uss_id="uss-local",
            state="accepted",
            priority="normal",
            conflict_policy="reject",
            volume4d=Volume4DPayload(x=[0, 10], y=[0, 10], z=[0, 60], time_start="2026-02-27T12:00:00Z", time_end="2026-02-27T12:20:00Z"),
        )
        out = post_operational_intent(payload)  # type: ignore[misc]
        self.assertEqual(out.get("status"), "success")
        intent = ((((out.get("result") or {}).get("upsert") or {}).get("intent") if isinstance(((out.get("result") or {}).get("upsert") or {}), dict) else {}) or {})
        self.assertTrue(isinstance(intent.get("signature"), dict))

    def test_external_intent_requires_signature(self) -> None:
        payload = OperationalIntentPayload(  # type: ignore[misc]
            intent_id="sig-peer-1",
            manager_uss_id="uss-peer-1",
            state="accepted",
            priority="normal",
            conflict_policy="reject",
            volume4d=Volume4DPayload(x=[20, 30], y=[20, 30], z=[0, 60], time_start="2026-02-27T12:00:00Z", time_end="2026-02-27T12:20:00Z"),
        )
        out = post_operational_intent(payload)  # type: ignore[misc]
        self.assertEqual(str(out.get("status")), "error")
        self.assertEqual(str(out.get("error")), "intent_signature_required")

    def test_external_intent_with_valid_signature(self) -> None:
        state = _get_security_controls()  # type: ignore[misc]
        state = register_peer_key(state, issuer="uss-peer-1", key_id="peer-k1", secret="peer-secret")
        _set_security_controls(state)  # type: ignore[misc]

        body = {
            "intent_id": "sig-peer-2",
            "manager_uss_id": "uss-peer-1",
            "state": "accepted",
            "priority": "normal",
            "conflict_policy": "reject",
            "ovn": None,
            "uss_base_url": "",
            "volume4d": {"x": [40.0, 50.0], "y": [40.0, 50.0], "z": [0.0, 60.0], "time_start": "2026-02-27T12:00:00Z", "time_end": "2026-02-27T12:20:00Z"},
            "constraints": {},
            "metadata": {},
        }
        sig = sign_payload(body, state=state, issuer="uss-peer-1")
        payload = OperationalIntentPayload(  # type: ignore[misc]
            intent_id="sig-peer-2",
            manager_uss_id="uss-peer-1",
            state="accepted",
            priority="normal",
            conflict_policy="reject",
            volume4d=Volume4DPayload(x=[40, 50], y=[40, 50], z=[0, 60], time_start="2026-02-27T12:00:00Z", time_end="2026-02-27T12:20:00Z"),
            signature=sig,
        )
        out = post_operational_intent(payload)  # type: ignore[misc]
        self.assertEqual(out.get("status"), "success")
        verify = ((out.get("result") or {}).get("signature_verification") if isinstance(out.get("result"), dict) else {})
        self.assertTrue(bool((verify or {}).get("ok")))


if __name__ == "__main__":
    unittest.main()
