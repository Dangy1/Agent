from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _yaml_scalar(value: Any) -> str:
    return str(value or "").strip().strip("\"'")


def list_available_profiles(profiles_dir: Path) -> List[str]:
    if not profiles_dir.exists():
        return []
    return sorted([p.stem for p in profiles_dir.glob("*.yaml") if p.is_file()])


def load_jurisdiction_profile(profile_id: str, profiles_dir: Path) -> Dict[str, Any]:
    pid = str(profile_id or "").strip().lower().replace(".yaml", "")
    if not pid:
        raise ValueError("jurisdiction_profile_required")
    path = profiles_dir / f"{pid}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"profile_not_found:{pid}")
    raw = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"profile_parse_failed:{pid}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"profile_invalid_format:{pid}")
    out = dict(parsed)
    out.setdefault("profile_id", pid)
    out.setdefault("name", pid)
    out.setdefault("version", "0.0.0")
    out["source_path"] = str(path)
    return out


def parse_rtm_requirements(raw: str) -> List[Dict[str, Any]]:
    requirements: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    active_list_key: str | None = None

    def _flush() -> None:
        nonlocal current
        if not isinstance(current, dict):
            return
        current.setdefault("id", "")
        current.setdefault("phase", "")
        current.setdefault("status", "unknown")
        current.setdefault("requirement", "")
        current.setdefault("code_paths", [])
        current.setdefault("tests", [])
        current.setdefault("evidence_ids", [])
        requirements.append(current)
        current = None

    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- id:"):
            _flush()
            current = {
                "id": _yaml_scalar(stripped.split(":", 1)[1]),
                "phase": "",
                "status": "unknown",
                "requirement": "",
                "code_paths": [],
                "tests": [],
                "evidence_ids": [],
                "notes": "",
            }
            active_list_key = None
            continue
        if current is None:
            continue
        if stripped in {"code_paths:", "tests:", "evidence_ids:"}:
            active_list_key = stripped[:-1]
            continue
        if stripped.startswith("- ") and active_list_key in {"code_paths", "tests", "evidence_ids"}:
            current[active_list_key].append(_yaml_scalar(stripped[2:]))  # type: ignore[index]
            continue
        if ":" in stripped:
            key, raw_value = stripped.split(":", 1)
            key = key.strip()
            value = _yaml_scalar(raw_value)
            if key in {"phase", "status", "requirement", "notes"}:
                current[key] = value
            active_list_key = None

    _flush()
    return [r for r in requirements if str(r.get("id", "")).strip()]


def build_certification_pack(
    *,
    profile: Dict[str, Any],
    rtm_requirements: List[Dict[str, Any]],
    conformance: Dict[str, Any] | None,
    evidence_index: List[Dict[str, Any]],
    release_id: str,
    candidate_version: str,
    approvals: List[Dict[str, Any]],
    notes: str | None = None,
) -> Dict[str, Any]:
    generated_at = _now_iso()
    available_evidence_ids = {
        str(item.get("evidence_id", "")).strip()
        for item in evidence_index
        if isinstance(item, dict) and str(item.get("evidence_id", "")).strip()
    }
    claims: List[Dict[str, Any]] = []
    claims_to_evidence: List[Dict[str, Any]] = []

    for req in rtm_requirements:
        req_id = str(req.get("id", "")).strip()
        status = str(req.get("status", "unknown")).strip().lower()
        required_evidence = [str(x).strip() for x in (req.get("evidence_ids") or []) if str(x).strip()]
        matched_evidence = [eid for eid in required_evidence if eid in available_evidence_ids]
        if status == "implemented" and (not required_evidence or len(matched_evidence) == len(required_evidence)):
            claim_status = "supported"
        elif status in {"implemented", "partial"}:
            claim_status = "partial"
        else:
            claim_status = "gap"
        claim_id = f"claim-{req_id.lower().replace('_', '-').replace(':', '-')}"
        claim = {
            "claim_id": claim_id,
            "requirement_id": req_id,
            "phase": str(req.get("phase", "")),
            "status": claim_status,
            "statement": str(req.get("requirement", "")),
        }
        claims.append(claim)
        claims_to_evidence.append(
            {
                "claim_id": claim_id,
                "requirement_id": req_id,
                "required_evidence_ids": required_evidence,
                "matched_evidence_ids": matched_evidence,
                "evidence_complete": len(required_evidence) == len(matched_evidence) if required_evidence else True,
            }
        )

    cyber_controls = profile.get("cyber_controls") if isinstance(profile.get("cyber_controls"), list) else []
    cyber_control_status = []
    for ctrl in cyber_controls:
        if not isinstance(ctrl, dict):
            continue
        ctrl_evidence = [str(x).strip() for x in (ctrl.get("evidence_ids") or []) if str(x).strip()]
        matched = [eid for eid in ctrl_evidence if eid in available_evidence_ids]
        cyber_control_status.append(
            {
                "control_id": str(ctrl.get("control_id", "")),
                "description": str(ctrl.get("description", "")),
                "required_evidence_ids": ctrl_evidence,
                "matched_evidence_ids": matched,
                "status": "implemented" if len(ctrl_evidence) == len(matched) else "gap",
            }
        )

    required_approvals = (
        profile.get("governance", {}).get("required_approvals", [])
        if isinstance(profile.get("governance"), dict)
        else []
    )
    approved_roles = {
        str(a.get("role", "")).strip().lower()
        for a in approvals
        if isinstance(a, dict) and str(a.get("status", "approved")).strip().lower() == "approved"
    }
    missing_approvals = [str(r).strip().lower() for r in required_approvals if str(r).strip().lower() not in approved_roles]

    supported = len([c for c in claims if c.get("status") == "supported"])
    partial = len([c for c in claims if c.get("status") == "partial"])
    gaps = len([c for c in claims if c.get("status") == "gap"])
    critical_findings = [c for c in claims if c.get("status") == "gap" and str(c.get("phase", "")).lower() in {"mvp", "pre-cert"}]
    conformance_passed = bool(isinstance(conformance, dict) and conformance.get("passed") is True)
    release_ready = bool(conformance_passed and gaps == 0 and not missing_approvals)

    return {
        "pack_id": f"cert-{uuid4().hex[:12]}",
        "generated_at": generated_at,
        "release_id": str(release_id or "release-local"),
        "candidate_version": str(candidate_version or "0.0.0-dev"),
        "jurisdiction_profile": {
            "profile_id": str(profile.get("profile_id", "")),
            "name": str(profile.get("name", "")),
            "version": str(profile.get("version", "")),
            "effective_date": profile.get("effective_date"),
            "source_path": profile.get("source_path"),
        },
        "summary": {
            "total_claims": len(claims),
            "supported_claims": supported,
            "partial_claims": partial,
            "gap_claims": gaps,
            "critical_findings": len(critical_findings),
            "conformance_passed": conformance_passed,
            "missing_approvals": missing_approvals,
            "release_ready": release_ready,
        },
        "safety_case": {
            "claims": claims,
            "critical_findings": critical_findings,
        },
        "cyber_controls": {
            "controls": cyber_control_status,
        },
        "incident_process": {
            "required_artifacts": (
                profile.get("incident_process", {}).get("required_artifacts", [])
                if isinstance(profile.get("incident_process"), dict)
                else []
            )
        },
        "continuity": (
            profile.get("continuity", {})
            if isinstance(profile.get("continuity"), dict)
            else {}
        ),
        "software_assurance": (
            profile.get("software_assurance", {})
            if isinstance(profile.get("software_assurance"), dict)
            else {}
        ),
        "governance": {
            "required_approvals": required_approvals,
            "provided_approvals": approvals,
            "missing_approvals": missing_approvals,
        },
        "evidence_index": {
            "claims_to_evidence": claims_to_evidence,
            "evidence_log_size": len(evidence_index),
            "evidence_ids": sorted(list(available_evidence_ids)),
        },
        "notes": str(notes or ""),
    }


__all__ = [
    "list_available_profiles",
    "load_jurisdiction_profile",
    "parse_rtm_requirements",
    "build_certification_pack",
]
