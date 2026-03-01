import unittest

from utm_agent.security_controls import (
    authorize_service_request,
    ensure_security_state,
    register_peer_key,
    rotate_signing_key,
    sign_payload,
    verify_signature,
)


class SecurityControlsModuleTests(unittest.TestCase):
    def test_sign_verify_and_rotation(self) -> None:
        state = ensure_security_state(None)
        payload = {"intent_id": "oi-1", "manager_uss_id": "uss-local"}
        sig = sign_payload(payload, state=state, issuer="utm-local")
        ok = verify_signature(payload, sig, state=state)
        self.assertTrue(bool(ok.get("ok")))

        rotated_state, new_key = rotate_signing_key(state, issuer="utm-local")
        self.assertTrue(str(new_key.get("key_id", "")).startswith("key-"))
        sig2 = sign_payload(payload, state=rotated_state, issuer="utm-local")
        ok2 = verify_signature(payload, sig2, state=rotated_state)
        self.assertTrue(bool(ok2.get("ok")))

    def test_register_peer_key_and_authorize(self) -> None:
        state = ensure_security_state(None)
        state = register_peer_key(state, issuer="uss-peer-1", key_id="peer-k1", secret="peer-secret")
        payload = {"intent_id": "oi-2", "manager_uss_id": "uss-peer-1"}
        peer_sig = sign_payload(payload, state=state, issuer="uss-peer-1")
        verify = verify_signature(payload, peer_sig, state=state)
        self.assertTrue(bool(verify.get("ok")))

        auth_state = ensure_security_state(
            {
                "service_tokens": {"tok-admin": ["admin"], "tok-dss-w": ["dss_write"]},
                "trust_store": state.get("trust_store"),
                "key_rotation_policy": state.get("key_rotation_policy"),
            }
        )
        denied = authorize_service_request(
            path="/api/utm/dss/operational-intents",
            method="POST",
            authorization_header="Bearer tok-none",
            state=auth_state,
            enforce=True,
        )
        self.assertFalse(bool(denied.get("ok")))
        allowed = authorize_service_request(
            path="/api/utm/dss/operational-intents",
            method="POST",
            authorization_header="Bearer tok-dss-w",
            state=auth_state,
            enforce=True,
        )
        self.assertTrue(bool(allowed.get("ok")))


if __name__ == "__main__":
    unittest.main()
