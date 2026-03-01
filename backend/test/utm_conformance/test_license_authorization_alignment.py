import unittest

from utm_agent.security_controls import authorize_service_request, ensure_security_state
from utm_agent.service import UTMApprovalStore


class LicenseAuthorizationAlignmentTests(unittest.TestCase):
    def test_license_check_exposes_authorization_scope(self) -> None:
        svc = UTMApprovalStore()
        svc.register_operator_license(
            operator_license_id="op-auth-1",
            license_class="BVLOS",
            uav_size_class="middle",
            expires_at="2099-01-01T00:00:00Z",
            active=True,
        )
        result = svc.check_operator_license(operator_license_id="op-auth-1", required_class="VLOS")
        self.assertTrue(bool(result.get("ok")))
        auth = result.get("authorization") if isinstance(result.get("authorization"), dict) else {}
        self.assertTrue(bool(auth.get("authorized")))
        self.assertIn("allowed_operations", auth)
        self.assertIn("launch", auth.get("allowed_operations") or [])

    def test_launch_validation_blocks_invalid_authorization(self) -> None:
        svc = UTMApprovalStore()
        approval = {
            "uav_id": "uav-1",
            "route_id": "route-1",
            "approved": True,
            "signature_verified": True,
            "expires_at": "2099-01-01T00:00:00Z",
            "checks": {},
            "authorization": {"authorized": False, "reason": "license_class_insufficient"},
        }
        check = svc.validate_approval_for_launch(approval, uav_id="uav-1", route_id="route-1")
        self.assertFalse(bool(check.get("ok")))
        self.assertEqual("approval_authorization_invalid", check.get("error"))

    def test_security_requires_utm_write_for_mutating_utm_ops(self) -> None:
        state = ensure_security_state(
            {
                "service_tokens": {
                    "tok-read": ["read"],
                    "tok-write": ["utm_write"],
                }
            }
        )
        denied = authorize_service_request(
            path="/api/utm/license",
            method="POST",
            authorization_header="Bearer tok-read",
            state=state,
            enforce=True,
        )
        self.assertFalse(bool(denied.get("ok")))
        self.assertEqual("utm_write", denied.get("required_role"))

        allowed = authorize_service_request(
            path="/api/utm/license",
            method="POST",
            authorization_header="Bearer tok-write",
            state=state,
            enforce=True,
        )
        self.assertTrue(bool(allowed.get("ok")))


if __name__ == "__main__":
    unittest.main()
