from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


OPEN_DEVIATION_STATUSES = {"open", "accepted", "in_review"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_deviation_record(
    *,
    release_id: str,
    category: str,
    severity: str,
    description: str,
    rationale: str = "",
    mitigation_plan: str = "",
    owner: str = "unknown",
    status: str = "open",
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    sev = str(severity or "major").strip().lower()
    if sev not in {"low", "major", "critical"}:
        sev = "major"
    stat = str(status or "open").strip().lower()
    if stat not in {"open", "accepted", "in_review", "resolved", "closed", "rejected"}:
        stat = "open"
    return {
        "deviation_id": f"dev-{uuid4().hex[:12]}",
        "release_id": str(release_id or "release-local"),
        "category": str(category or "compliance"),
        "severity": sev,
        "status": stat,
        "description": str(description or ""),
        "rationale": str(rationale or ""),
        "mitigation_plan": str(mitigation_plan or ""),
        "owner": str(owner or "unknown"),
        "metadata": dict(metadata or {}),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def resolve_deviation_record(
    deviation: Dict[str, Any],
    *,
    status: str = "resolved",
    resolver: str = "unknown",
    resolution_note: str = "",
) -> Dict[str, Any]:
    out = dict(deviation or {})
    next_status = str(status or "resolved").strip().lower()
    if next_status not in {"resolved", "closed", "rejected"}:
        next_status = "resolved"
    out["status"] = next_status
    out["updated_at"] = _now_iso()
    out["resolution"] = {
        "resolver": str(resolver or "unknown"),
        "note": str(resolution_note or ""),
        "resolved_at": _now_iso(),
    }
    return out


def evaluate_release_gate(
    *,
    release_id: str,
    pack: Dict[str, Any] | None,
    required_approvals: List[str] | None,
    deviations: List[Dict[str, Any]],
    latest_conformance: Dict[str, Any] | None,
    require_signed_campaign_report: bool = False,
    campaign_report: Dict[str, Any] | None = None,
    enforce_critical_findings: bool = False,
) -> Dict[str, Any]:
    summary = (pack.get("summary") if isinstance(pack, dict) and isinstance(pack.get("summary"), dict) else {}) if isinstance(pack, dict) else {}
    pack_missing = [str(x).strip().lower() for x in (summary.get("missing_approvals") or []) if str(x).strip()]
    requested_required = [str(x).strip().lower() for x in (required_approvals or []) if str(x).strip()]

    governance = pack.get("governance") if isinstance(pack, dict) and isinstance(pack.get("governance"), dict) else {}
    provided = governance.get("provided_approvals") if isinstance(governance, dict) and isinstance(governance.get("provided_approvals"), list) else []
    provided_roles = {
        str(a.get("role", "")).strip().lower()
        for a in provided
        if isinstance(a, dict) and str(a.get("status", "approved")).strip().lower() == "approved"
    }

    missing_approvals = list(pack_missing)
    if requested_required:
        for role in requested_required:
            if role not in provided_roles and role not in missing_approvals:
                missing_approvals.append(role)
    missing_approvals = sorted(set(missing_approvals))

    conformance_passed = bool(summary.get("conformance_passed") is True)
    if not conformance_passed and isinstance(latest_conformance, dict):
        conformance_passed = bool(latest_conformance.get("passed") is True)

    critical_findings = int(summary.get("critical_findings", 0) or 0)
    release_ready_hint = bool(summary.get("release_ready") is True)

    open_critical_deviations = [
        d
        for d in deviations
        if isinstance(d, dict)
        and str(d.get("release_id", "")).strip() == str(release_id or "release-local")
        and str(d.get("severity", "")).strip().lower() == "critical"
        and str(d.get("status", "")).strip().lower() in OPEN_DEVIATION_STATUSES
    ]

    signed_campaign_ok = True
    if require_signed_campaign_report:
        signed_campaign_ok = bool(
            isinstance(campaign_report, dict)
            and isinstance(campaign_report.get("signature"), dict)
            and str((campaign_report.get("signature") or {}).get("status", "")).strip().lower() == "signed"
        )

    reasons: List[str] = []
    if not conformance_passed:
        reasons.append("latest_conformance_not_passed")
    if enforce_critical_findings and critical_findings > 0:
        reasons.append("critical_findings_present")
    if missing_approvals:
        reasons.append("missing_required_approvals")
    if open_critical_deviations:
        reasons.append("open_critical_deviations_present")
    if not signed_campaign_ok:
        reasons.append("campaign_report_not_signed")
    decision = "allow" if not reasons else "block"
    return {
        "gate_id": f"gate-{uuid4().hex[:12]}",
        "evaluated_at": _now_iso(),
        "release_id": str(release_id or "release-local"),
        "decision": decision,
        "reasons": reasons,
        "checks": {
            "conformance_passed": conformance_passed,
            "critical_findings": critical_findings,
            "release_ready_hint": release_ready_hint,
            "missing_approvals": missing_approvals,
            "open_critical_deviations": len(open_critical_deviations),
            "signed_campaign_report_ok": signed_campaign_ok,
        },
        "inputs": {
            "pack_id": (pack.get("pack_id") if isinstance(pack, dict) else None),
            "required_approvals": requested_required,
            "require_signed_campaign_report": bool(require_signed_campaign_report),
            "enforce_critical_findings": bool(enforce_critical_findings),
        },
    }


__all__ = [
    "create_deviation_record",
    "resolve_deviation_record",
    "evaluate_release_gate",
]
