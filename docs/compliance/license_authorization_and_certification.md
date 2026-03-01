# License, Authorization, And Certification Alignment

This document records the current UTM/UAV alignment for license handling, authorization enforcement, and certification artifacts.

## 1) Backend Alignment

### UTM authorization roles

- Service auth now distinguishes generic UTM write operations with role `utm_write`.
- Endpoints under `/api/utm` with `POST/PUT/DELETE` require `utm_write` unless already covered by stronger roles (`dss_write`, `security_admin`, `compliance_admin`, `conformance_run`).
- Default dev token (`local-dev-token`) includes `utm_write`.

### License-to-authorization mapping

- `UTMApprovalStore.check_operator_license` now returns an explicit `authorization` object.
- `authorization` includes:
  - `authorized` (bool)
  - `required_class`
  - `actual_license_class`
  - `operator_license_id`
  - `allowed_operations` (derived from license class)
- Failure responses include a structured authorization reason (`license_not_found`, `license_expired`, `license_class_insufficient`, etc.).

### Launch-time authorization enforcement

- `verify_flight_plan` embeds authorization context in UTM approval records.
- `validate_approval_for_launch` blocks launch when approval authorization is invalid.
- UAV flight gate adds checks for invalid authorization in:
  - approval-level authorization
  - operator-license check authorization

## 2) Frontend Alignment

### Shared token flow

- Frontend shared state now includes `utmAuthToken`.
- UTM and UAV pages both read/write this token and use it for UTM protected endpoints.

### UTM page

- Added UI input: `UTM Bearer Token`.
- Token is applied to protected UTM endpoints (DSS, sync, security/compliance paths).
- Added certification controls:
  - compliance export action
  - certification pack generation action
  - profile/release/version inputs

### UAV page

- Added UI input: `UTM Bearer Token`.
- Token is applied when UAV page fetches UTM backend state/sync/live source.

## 3) Certification Artifacts (Saved Documents)

Generated profile-aligned certification packs and summaries are stored in:

- `docs/compliance/certification/README.md`
- `docs/compliance/certification/packs/us_faa_ntap_certification_pack.json`
- `docs/compliance/certification/packs/eu_ussp_2021_664_certification_pack.json`
- `docs/compliance/certification/packs/icao_framework_alignment_certification_pack.json`
- `docs/compliance/certification/us_faa_ntap_certification_summary.md`
- `docs/compliance/certification/eu_ussp_2021_664_certification_summary.md`
- `docs/compliance/certification/icao_framework_alignment_certification_summary.md`

## 4) Regenerate Certification Documents

Run from repo root:

```bash
PYTHONPATH=backend python backend/utm_agent/generate_certification_documents.py
```

This regenerates all certification JSON packs and Markdown summaries from:

- `profiles/*.yaml`
- `docs/compliance/rtm.yaml`
