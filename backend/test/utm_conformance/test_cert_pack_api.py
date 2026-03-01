import unittest

try:
    from utm_agent.api import (
        CertificationApprovalPayload,
        CertificationPackGeneratePayload,
        PROJECT_ROOT,
        export_certification_pack_docs,
        generate_certification_pack,
        get_certification_pack,
        list_certification_doc_exports,
        get_certification_profiles,
    )
except Exception as exc:  # pragma: no cover - optional deps in local env
    CertificationApprovalPayload = None
    CertificationPackGeneratePayload = None
    PROJECT_ROOT = None
    export_certification_pack_docs = None
    generate_certification_pack = None
    get_certification_pack = None
    list_certification_doc_exports = None
    get_certification_profiles = None
    _IMPORT_ERROR_MESSAGE = repr(exc)
else:
    _IMPORT_ERROR_MESSAGE = ""


class CertificationPackApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if generate_certification_pack is None:
            raise unittest.SkipTest(f"utm_agent.api import unavailable: {_IMPORT_ERROR_MESSAGE}")

    def test_profiles_endpoint_and_generate_fetch_pack(self) -> None:
        profiles = get_certification_profiles()  # type: ignore[misc]
        self.assertEqual(profiles.get("status"), "success")
        items = ((profiles.get("result") or {}).get("items") if isinstance(profiles.get("result"), dict) else [])
        self.assertTrue(any(isinstance(x, dict) and x.get("profile_id") == "us_faa_ntap" for x in items))

        payload = CertificationPackGeneratePayload(  # type: ignore[misc]
            jurisdiction_profile="us_faa_ntap",
            release_id="rel-cert-api",
            candidate_version="2.0.0",
            approvals=[
                CertificationApprovalPayload(role="safety", approver="alice", status="approved"),
                CertificationApprovalPayload(role="security", approver="bob", status="approved"),
                CertificationApprovalPayload(role="compliance", approver="carol", status="approved"),
                CertificationApprovalPayload(role="release_manager", approver="dave", status="approved"),
            ],
            notes="api-test",
        )
        created = generate_certification_pack(payload)  # type: ignore[misc]
        self.assertEqual(created.get("status"), "success")
        result = created.get("result") if isinstance(created.get("result"), dict) else {}
        pack_id = str(result.get("pack_id") or "")
        self.assertTrue(pack_id.startswith("cert-"))

        fetched = get_certification_pack(pack_id)  # type: ignore[misc]
        self.assertEqual(fetched.get("status"), "success")
        fetched_result = fetched.get("result") if isinstance(fetched.get("result"), dict) else {}
        self.assertEqual(str(fetched_result.get("pack_id") or ""), pack_id)

        exported = export_certification_pack_docs(pack_id)  # type: ignore[misc]
        self.assertEqual(exported.get("status"), "success")
        export_result = exported.get("result") if isinstance(exported.get("result"), dict) else {}
        self.assertTrue(str(export_result.get("export_id") or "").startswith("cert-exp-"))
        downloads = export_result.get("downloads") if isinstance(export_result.get("downloads"), list) else []
        self.assertEqual(len(downloads), 2)
        for item in downloads:
            row = item if isinstance(item, dict) else {}
            rel = str(row.get("relative_path") or "")
            self.assertTrue(rel)
            self.assertTrue((PROJECT_ROOT / rel).exists())  # type: ignore[operator]

        exports = list_certification_doc_exports(limit=10)  # type: ignore[misc]
        self.assertEqual(exports.get("status"), "success")
        items = ((exports.get("result") or {}).get("items") if isinstance(exports.get("result"), dict) else [])
        self.assertTrue(any(isinstance(x, dict) and str(x.get("export_id") or "") == str(export_result.get("export_id") or "") for x in items))


if __name__ == "__main__":
    unittest.main()
