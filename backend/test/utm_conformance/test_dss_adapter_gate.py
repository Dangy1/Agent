import unittest

from utm_agent.dss_adapter import HttpDSSAdapter, InMemoryDSSAdapter, build_dss_adapter


class DSSAdapterGateTests(unittest.TestCase):
    def test_build_local_adapter(self) -> None:
        adapter = build_dss_adapter("local")
        self.assertIsInstance(adapter, InMemoryDSSAdapter)

    def test_build_http_adapter_without_gate(self) -> None:
        adapter = build_dss_adapter("http", base_url="http://127.0.0.1:8010", require_local_conformance=False)
        self.assertIsInstance(adapter, HttpDSSAdapter)

    def test_http_adapter_requires_local_conformance_when_enabled(self) -> None:
        with self.assertRaises(ValueError):
            build_dss_adapter(
                "http",
                base_url="http://127.0.0.1:8010",
                require_local_conformance=True,
                local_conformance={"passed": False},
            )
        adapter = build_dss_adapter(
            "http",
            base_url="http://127.0.0.1:8010",
            require_local_conformance=True,
            local_conformance={"passed": True},
        )
        self.assertIsInstance(adapter, HttpDSSAdapter)


if __name__ == "__main__":
    unittest.main()
