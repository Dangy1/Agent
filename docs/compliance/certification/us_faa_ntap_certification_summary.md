# Certification Alignment: US FAA NTAP

- Profile ID: `us_faa_ntap`
- Profile Version: `2026.1`
- Profile Effective Date: `2026-01-01`
- Generated At (UTC): `2026-02-27T12:34:15.302720Z`
- Pack ID: `cert-deebd0f8aca8`
- Release ID: `release-local`
- Candidate Version: `0.1.0-aligned`

## Pack Summary

- Total Claims: `17`
- Supported Claims: `16`
- Partial Claims: `1`
- Gap Claims: `0`
- Critical Findings: `0`
- Conformance Passed: `True`
- Missing Approvals: `-`
- Release Ready: `True`

## Cyber Controls

| Control ID | Status | Evidence Required | Evidence Matched |
| --- | --- | --- | --- |
| `FAA-CY-01` | `implemented` | `compliance_export` | `compliance_export` |
| `FAA-CY-02` | `gap` | `dss_upsert_operational_intent, route_checks` | `dss_upsert_operational_intent` |

## Critical Requirement Findings

- None

## Profile Artifact Coverage

| Artifact | Path | Present |
| --- | --- | --- |
| `authority_contact_matrix` | `docs/operations/authority_contact_matrix.md` | `True` |
| `change_log` | `docs/operations/change_log.md` | `False` |
| `incident_reporting_sop` | `docs/operations/incident_reporting_sop.md` | `True` |
| `incident_response_plan` | `docs/operations/incident_response_plan.md` | `True` |
| `release_checklist` | `docs/operations/release_checklist.md` | `False` |
| `sbom` | `docs/operations/sbom.md` | `False` |
| `test_report` | `docs/operations/test_report.md` | `False` |
| `continuity_profile` | `profile:us_faa_ntap` | `True` |

