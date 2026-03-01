import unittest

try:
    from mission_supervisor_agent.domain_dispatch import dispatch_domain_agent

    _DISPATCH_READY = True
    _DISPATCH_SKIP_REASON = ""
except Exception as exc:  # pragma: no cover
    dispatch_domain_agent = None  # type: ignore[assignment]
    _DISPATCH_READY = False
    _DISPATCH_SKIP_REASON = f"dispatch dependencies unavailable: {exc}"


@unittest.skipUnless(_DISPATCH_READY, _DISPATCH_SKIP_REASON)
class MissionSupervisorDispatchDomainTests(unittest.TestCase):
    def test_dispatch_dss_state(self) -> None:
        out = dispatch_domain_agent({"domain": "dss", "op": "state", "params": {}}, {})
        self.assertEqual(str(out.get("status")), "success")
        self.assertEqual(str(out.get("agent")), "dss")

    def test_dispatch_uss_state(self) -> None:
        out = dispatch_domain_agent({"domain": "uss", "op": "state", "params": {}}, {})
        self.assertEqual(str(out.get("status")), "success")
        self.assertEqual(str(out.get("agent")), "uss")


if __name__ == "__main__":
    unittest.main()
