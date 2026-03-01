import copy
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    import uav_agent.api_routes_uav as api_routes_uav
    import uav_agent.api_shared as api_shared
    from uav_agent.api_models import ApprovalPayload
    from uav_agent.api_shared import (
        SIM,
        UTM_DB_MIRROR,
        UTM_SERVICE,
        _flight_control_gate_issues,
        _save_uav_utm_session,
        _upsert_local_dss_intent_for_uav,
    )
    from utm_agent.dss_gateway import gateway_upsert_operational_intent
except Exception as exc:  # pragma: no cover - optional deps may be missing in local env
    api_routes_uav = None
    api_shared = None
    ApprovalPayload = None
    SIM = None
    UTM_DB_MIRROR = None
    UTM_SERVICE = None
    _flight_control_gate_issues = None
    _save_uav_utm_session = None
    _upsert_local_dss_intent_for_uav = None
    gateway_upsert_operational_intent = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class UavLaunchSubmitGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if (
            api_routes_uav is None
            or api_shared is None
            or ApprovalPayload is None
            or SIM is None
            or UTM_DB_MIRROR is None
            or UTM_SERVICE is None
            or _flight_control_gate_issues is None
            or _save_uav_utm_session is None
            or _upsert_local_dss_intent_for_uav is None
            or gateway_upsert_operational_intent is None
        ):
            raise unittest.SkipTest(f"uav gate test imports unavailable: {_IMPORT_ERROR_MESSAGE}")
        cls._orig_route_log = api_routes_uav._log_uav_action
        cls._orig_shared_log = api_shared._log_uav_action
        cls._orig_refresh_utm_mirror = api_routes_uav._refresh_utm_mirror_from_real_service
        api_routes_uav._log_uav_action = lambda *args, **kwargs: {"agent": "uav", "revision": 0, "updated_at": "test"}
        api_shared._log_uav_action = lambda *args, **kwargs: {"agent": "uav", "revision": 0, "updated_at": "test"}
        api_routes_uav._refresh_utm_mirror_from_real_service = lambda **kwargs: {"status": "skipped", "reason": "test"}

    @classmethod
    def tearDownClass(cls) -> None:
        if api_routes_uav is not None:
            api_routes_uav._log_uav_action = cls._orig_route_log
            api_routes_uav._refresh_utm_mirror_from_real_service = cls._orig_refresh_utm_mirror
        if api_shared is not None:
            api_shared._log_uav_action = cls._orig_shared_log

    def setUp(self) -> None:
        self.user_id = f"user-{uuid.uuid4().hex[:8]}"
        self.uav_id = f"uav-{uuid.uuid4().hex[:8]}"
        self.airspace = "sector-A3"
        self.route_id = f"{self.uav_id}-route-gate"
        self.waypoints = [
            {"x": 100.0, "y": 40.0, "z": 40.0, "action": "transit"},
            {"x": 150.0, "y": 100.0, "z": 40.0, "action": "transit"},
            {"x": 200.0, "y": 40.0, "z": 40.0, "action": "photo"},
        ]
        self._old_intents = UTM_DB_MIRROR.get_state("dss_operational_intents")
        self._old_nfz = [dict(z) for z in UTM_SERVICE.no_fly_zones]
        self._old_approvals = dict(UTM_SERVICE.approvals)
        UTM_DB_MIRROR.set_state("dss_operational_intents", {})
        UTM_SERVICE.no_fly_zones = []

    def tearDown(self) -> None:
        if isinstance(self._old_intents, dict):
            UTM_DB_MIRROR.set_state("dss_operational_intents", self._old_intents)
        else:
            UTM_DB_MIRROR.delete_state("dss_operational_intents")
        UTM_SERVICE.no_fly_zones = [dict(z) for z in self._old_nfz]
        UTM_SERVICE.approvals = dict(self._old_approvals)

    def _window(self) -> tuple[str, str]:
        start = datetime.now(timezone.utc) + timedelta(minutes=5)
        end = start + timedelta(minutes=20)
        return (
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )

    def _seed_launch_gate_session(self, *, expires_at: str, session_ovn_override: str | None = None) -> None:
        start_at, end_at = self._window()
        SIM.plan_route(self.uav_id, route_id=self.route_id, waypoints=self.waypoints)
        SIM.ingest_live_state(
            self.uav_id,
            route_id=self.route_id,
            waypoints=self.waypoints,
            battery_pct=95.0,
            source="simulated",
            source_meta={"test_case": "gate_seed"},
        )
        dss_result = _upsert_local_dss_intent_for_uav(
            user_id=self.user_id,
            uav_id=self.uav_id,
            route_id=self.route_id,
            waypoints=[dict(w) for w in self.waypoints],
            airspace_segment=self.airspace,
            state="contingent",
            conflict_policy="reject",
            source="test_seed",
            lifecycle_phase="approval_requested",
            planned_start_at=start_at,
            planned_end_at=end_at,
        )
        self.assertEqual(dss_result.get("status"), "success")
        session_dss = copy.deepcopy(dss_result)
        if session_ovn_override is not None:
            if isinstance(session_dss.get("intent"), dict):
                session_dss["intent"]["ovn"] = session_ovn_override
            else:
                session_dss["ovn"] = session_ovn_override
        approval = {
            "approval_id": f"approval-{self.uav_id}",
            "issuer": "UTM",
            "uav_id": self.uav_id,
            "route_id": self.route_id,
            "airspace_segment": self.airspace,
            "approved": True,
            "permissions": ["launch", "transit"],
            "expires_at": expires_at,
            "signature_verified": True,
            "checks": {
                "route_bounds": {"ok": True},
                "weather": {"ok": True},
                "no_fly_zone": {"ok": True},
                "regulations": {"ok": True},
                "time_window": {"ok": True},
                "operator_license": {"ok": True, "authorization": {"authorized": True}},
            },
            "authorization": {"authorized": True},
        }
        _save_uav_utm_session(
            user_id=self.user_id,
            uav_id=self.uav_id,
            utm_approval=approval,
            utm_geofence_result={"ok": True, "geofence_ok": True},
            utm_dss_result=session_dss,
        )

    def test_launch_gate_rejects_expired_approval(self) -> None:
        expired = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        self._seed_launch_gate_session(expires_at=expired)
        issues = _flight_control_gate_issues(self.uav_id, action="launch", user_id=self.user_id)
        self.assertTrue(any("UTM approval expired" in str(issue) for issue in issues))

    def test_launch_gate_rejects_stale_dss_ovn(self) -> None:
        expires = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
        self._seed_launch_gate_session(expires_at=expires, session_ovn_override="ovn-stale-from-session")
        issues = _flight_control_gate_issues(self.uav_id, action="launch", user_id=self.user_id)
        self.assertTrue(any("DSS OVN stale" in str(issue) for issue in issues))

    def test_submit_gate_rejects_low_battery_before_verify(self) -> None:
        SIM.plan_route(self.uav_id, route_id=self.route_id, waypoints=self.waypoints)
        SIM.ingest_live_state(
            self.uav_id,
            route_id=self.route_id,
            waypoints=self.waypoints,
            battery_pct=10.0,
            source="simulated",
            source_meta={"test_case": "submit_gate_battery"},
        )
        body = api_routes_uav.post_utm_submit_mission(
            ApprovalPayload(
                user_id=self.user_id,
                uav_id=self.uav_id,
                airspace_segment=self.airspace,
                operator_license_id="op-001",
                required_license_class="VLOS",
                requested_speed_mps=12.0,
            )
        )
        aggregate = body.get("result") if isinstance(body, dict) else {}
        submit_gate = aggregate.get("submit_gate") if isinstance(aggregate, dict) else {}
        self.assertIsInstance(submit_gate, dict)
        self.assertFalse(bool(submit_gate.get("battery_ok")))
        self.assertEqual(str((aggregate.get("verify_from_uav") or {}).get("status")), "skipped")
        self.assertEqual(str((aggregate.get("verify_from_uav") or {}).get("reason")), "battery_low")
        self.assertEqual(str((aggregate.get("approval_request") or {}).get("reason")), "battery_low")


if __name__ == "__main__":
    unittest.main()
