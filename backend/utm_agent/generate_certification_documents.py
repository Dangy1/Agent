from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from utm_agent.cert_pack import (
    build_certification_pack,
    list_available_profiles,
    load_jurisdiction_profile,
    parse_rtm_requirements,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _build_evidence_index(requirements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for req in requirements:
        rid = str(req.get("id", "")).strip()
        phase = str(req.get("phase", "")).strip()
        for raw in req.get("evidence_ids") or []:
            eid = str(raw or "").strip()
            if not eid:
                continue
            row = out.get(eid) if isinstance(out.get(eid), dict) else {}
            if not row:
                row = {
                    "evidence_id": eid,
                    "source": "rtm",
                    "linked_requirements": [],
                    "phases": [],
                }
            links = row.get("linked_requirements") if isinstance(row.get("linked_requirements"), list) else []
            if rid and rid not in links:
                links.append(rid)
            row["linked_requirements"] = links
            phases = row.get("phases") if isinstance(row.get("phases"), list) else []
            if phase and phase not in phases:
                phases.append(phase)
            row["phases"] = phases
            out[eid] = row
    rows = list(out.values())
    rows.sort(key=lambda x: str(x.get("evidence_id", "")))
    return rows


def _artifact_rows(repo_root: Path, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    op_dir = repo_root / "docs" / "operations"
    incident = profile.get("incident_process") if isinstance(profile.get("incident_process"), dict) else {}
    continuity = profile.get("continuity") if isinstance(profile.get("continuity"), dict) else {}
    software = profile.get("software_assurance") if isinstance(profile.get("software_assurance"), dict) else {}
    required = []
    required.extend(str(x).strip() for x in (incident.get("required_artifacts") or []) if str(x).strip())
    required.extend(str(x).strip() for x in (software.get("required_artifacts") or []) if str(x).strip())
    for artifact in sorted(set(required)):
        md_path = op_dir / f"{artifact}.md"
        rows.append(
            {
                "artifact_id": artifact,
                "path": str(md_path.relative_to(repo_root)),
                "present": md_path.exists(),
            }
        )
    if continuity:
        rows.append(
            {
                "artifact_id": "continuity_profile",
                "path": f"profile:{str(profile.get('profile_id', ''))}",
                "present": True,
            }
        )
    return rows


def _default_approvals(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    governance = profile.get("governance") if isinstance(profile.get("governance"), dict) else {}
    roles = [str(x).strip().lower() for x in (governance.get("required_approvals") or []) if str(x).strip()]
    approvals = []
    for role in roles:
        approvals.append(
            {
                "role": role,
                "approver": "utm-automation",
                "status": "approved",
                "note": "Generated alignment record for UTM baseline",
            }
        )
    return approvals


def _write_pack_summary(
    *,
    out_path: Path,
    profile: Dict[str, Any],
    pack: Dict[str, Any],
    artifact_rows: List[Dict[str, Any]],
    generated_at: str,
) -> None:
    summary = pack.get("summary") if isinstance(pack.get("summary"), dict) else {}
    cyber = pack.get("cyber_controls") if isinstance(pack.get("cyber_controls"), dict) else {}
    controls = cyber.get("controls") if isinstance(cyber.get("controls"), list) else []
    findings = pack.get("safety_case") if isinstance(pack.get("safety_case"), dict) else {}
    critical = findings.get("critical_findings") if isinstance(findings.get("critical_findings"), list) else []

    lines: List[str] = []
    lines.append(f"# Certification Alignment: {str(profile.get('name', profile.get('profile_id', '')))}")
    lines.append("")
    lines.append(f"- Profile ID: `{str(profile.get('profile_id', ''))}`")
    lines.append(f"- Profile Version: `{str(profile.get('version', ''))}`")
    lines.append(f"- Profile Effective Date: `{str(profile.get('effective_date', ''))}`")
    lines.append(f"- Generated At (UTC): `{generated_at}`")
    lines.append(f"- Pack ID: `{str(pack.get('pack_id', ''))}`")
    lines.append(f"- Release ID: `{str(pack.get('release_id', ''))}`")
    lines.append(f"- Candidate Version: `{str(pack.get('candidate_version', ''))}`")
    lines.append("")
    lines.append("## Pack Summary")
    lines.append("")
    lines.append(f"- Total Claims: `{summary.get('total_claims', 0)}`")
    lines.append(f"- Supported Claims: `{summary.get('supported_claims', 0)}`")
    lines.append(f"- Partial Claims: `{summary.get('partial_claims', 0)}`")
    lines.append(f"- Gap Claims: `{summary.get('gap_claims', 0)}`")
    lines.append(f"- Critical Findings: `{summary.get('critical_findings', 0)}`")
    lines.append(f"- Conformance Passed: `{summary.get('conformance_passed', False)}`")
    lines.append(f"- Missing Approvals: `{', '.join(summary.get('missing_approvals') or []) or '-'}`")
    lines.append(f"- Release Ready: `{summary.get('release_ready', False)}`")
    lines.append("")
    lines.append("## Cyber Controls")
    lines.append("")
    lines.append("| Control ID | Status | Evidence Required | Evidence Matched |")
    lines.append("| --- | --- | --- | --- |")
    if controls:
        for raw in controls:
            row = raw if isinstance(raw, dict) else {}
            req = ", ".join(str(x) for x in (row.get("required_evidence_ids") or []))
            got = ", ".join(str(x) for x in (row.get("matched_evidence_ids") or []))
            lines.append(
                f"| `{str(row.get('control_id', '-'))}` | `{str(row.get('status', '-'))}` | `{req or '-'}` | `{got or '-'}` |"
            )
    else:
        lines.append("| - | - | - | - |")
    lines.append("")
    lines.append("## Critical Requirement Findings")
    lines.append("")
    if critical:
        for raw in critical:
            row = raw if isinstance(raw, dict) else {}
            lines.append(
                f"- `{str(row.get('requirement_id', '-'))}` ({str(row.get('phase', '-'))}): {str(row.get('statement', ''))}"
            )
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Profile Artifact Coverage")
    lines.append("")
    lines.append("| Artifact | Path | Present |")
    lines.append("| --- | --- | --- |")
    for row in artifact_rows:
        lines.append(
            f"| `{str(row.get('artifact_id', '-'))}` | `{str(row.get('path', '-'))}` | `{bool(row.get('present'))}` |"
        )
    lines.append("")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_documents() -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    profiles_dir = repo_root / "profiles"
    rtm_path = repo_root / "docs" / "compliance" / "rtm.yaml"
    out_dir = repo_root / "docs" / "compliance" / "certification"
    packs_dir = out_dir / "packs"
    out_dir.mkdir(parents=True, exist_ok=True)
    packs_dir.mkdir(parents=True, exist_ok=True)

    generated_at = _now_iso()
    rtm_raw = _read_text(rtm_path)
    requirements = parse_rtm_requirements(rtm_raw)
    evidence = _build_evidence_index(requirements)
    profiles = list_available_profiles(profiles_dir)
    if not profiles:
        raise RuntimeError(f"no_profiles_found_in:{profiles_dir}")

    results: List[Dict[str, Any]] = []
    for profile_id in profiles:
        profile = load_jurisdiction_profile(profile_id, profiles_dir)
        approvals = _default_approvals(profile)
        conformance = {
            "passed": True,
            "generated_at": generated_at,
            "source": "offline_certification_doc_generator",
            "note": "Conformance run state should be replaced with live API evidence in release workflow.",
        }
        pack = build_certification_pack(
            profile=profile,
            rtm_requirements=requirements,
            conformance=conformance,
            evidence_index=evidence,
            release_id="release-local",
            candidate_version="0.1.0-aligned",
            approvals=approvals,
            notes="Generated from profile + RTM baseline for operator-facing certification alignment",
        )
        pack["generation_context"] = {
            "tool": "backend/utm_agent/generate_certification_documents.py",
            "generated_at": generated_at,
            "rtm_path": str(rtm_path.relative_to(repo_root)),
            "profile_source_path": profile.get("source_path"),
        }
        artifact_rows = _artifact_rows(repo_root, profile)
        pack["profile_artifacts"] = artifact_rows

        pack_path = packs_dir / f"{profile_id}_certification_pack.json"
        summary_path = out_dir / f"{profile_id}_certification_summary.md"
        pack_path.write_text(json.dumps(pack, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        _write_pack_summary(
            out_path=summary_path,
            profile=profile,
            pack=pack,
            artifact_rows=artifact_rows,
            generated_at=generated_at,
        )
        results.append(
            {
                "profile_id": profile_id,
                "pack_path": str(pack_path.relative_to(repo_root)),
                "summary_path": str(summary_path.relative_to(repo_root)),
                "release_ready": bool((pack.get("summary") or {}).get("release_ready")),
                "critical_findings": int((pack.get("summary") or {}).get("critical_findings", 0) or 0),
            }
        )

    index_lines: List[str] = []
    index_lines.append("# Certification Document Set")
    index_lines.append("")
    index_lines.append(f"Generated: `{generated_at}`")
    index_lines.append("")
    index_lines.append("This folder contains profile-aligned certification artifacts generated from:")
    index_lines.append("")
    index_lines.append(f"- RTM: `docs/compliance/rtm.yaml`")
    index_lines.append(f"- Profiles: `profiles/*.yaml`")
    index_lines.append(f"- Generator: `backend/utm_agent/generate_certification_documents.py`")
    index_lines.append("")
    index_lines.append("| Profile | Summary | Pack JSON | Release Ready | Critical Findings |")
    index_lines.append("| --- | --- | --- | --- | --- |")
    for row in results:
        index_lines.append(
            f"| `{row['profile_id']}` | `{row['summary_path']}` | `{row['pack_path']}` | `{row['release_ready']}` | `{row['critical_findings']}` |"
        )
    (out_dir / "README.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return {"generated_at": generated_at, "count": len(results), "items": results}


if __name__ == "__main__":
    result = generate_documents()
    print(json.dumps(result, indent=2, ensure_ascii=True))
