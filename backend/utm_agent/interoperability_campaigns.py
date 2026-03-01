from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_campaign(
    *,
    campaign_id: str | None = None,
    name: str,
    jurisdiction_profile: str,
    release_id: str,
    partners: List[str],
    scenarios: List[str],
    scheduled_start: str | None = None,
    scheduled_end: str | None = None,
    created_by: str = "utm",
    notes: str | None = None,
) -> Dict[str, Any]:
    cid = str(campaign_id or f"camp-{uuid4().hex[:12]}")
    return {
        "campaign_id": cid,
        "name": str(name or "interoperability_campaign"),
        "jurisdiction_profile": str(jurisdiction_profile or "us_faa_ntap"),
        "release_id": str(release_id or "release-local"),
        "partners": [str(p).strip() for p in partners if str(p).strip()],
        "scenarios": [str(s).strip() for s in scenarios if str(s).strip()],
        "status": "running",
        "scheduled_start": scheduled_start,
        "scheduled_end": scheduled_end,
        "created_by": str(created_by or "utm"),
        "notes": str(notes or ""),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "run_records": [],
        "report_signature": None,
    }


def append_campaign_run(
    campaign: Dict[str, Any],
    *,
    partner_id: str,
    scenario_id: str,
    status: str,
    summary: str = "",
    evidence_ids: List[str] | None = None,
    metrics: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    out = dict(campaign or {})
    rows = out.get("run_records") if isinstance(out.get("run_records"), list) else []
    stat = str(status or "passed").strip().lower()
    if stat not in {"passed", "failed", "blocked", "skipped"}:
        stat = "failed"
    rows.append(
        {
            "run_id": f"run-{uuid4().hex[:12]}",
            "recorded_at": _now_iso(),
            "partner_id": str(partner_id or ""),
            "scenario_id": str(scenario_id or ""),
            "status": stat,
            "summary": str(summary or ""),
            "evidence_ids": [str(x).strip() for x in (evidence_ids or []) if str(x).strip()],
            "metrics": dict(metrics or {}),
        }
    )
    out["run_records"] = rows
    out["updated_at"] = _now_iso()
    return out


def sign_campaign_report(
    campaign: Dict[str, Any],
    *,
    signed_by: str,
    signature_ref: str,
    decision: str = "accepted",
    note: str = "",
) -> Dict[str, Any]:
    out = dict(campaign or {})
    out["report_signature"] = {
        "status": "signed",
        "signed_by": str(signed_by or "unknown"),
        "signature_ref": str(signature_ref or ""),
        "decision": str(decision or "accepted"),
        "note": str(note or ""),
        "signed_at": _now_iso(),
    }
    out["status"] = "completed"
    out["updated_at"] = _now_iso()
    return out


def build_campaign_report(
    campaign: Dict[str, Any],
    *,
    compliance_export: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    run_records = campaign.get("run_records") if isinstance(campaign.get("run_records"), list) else []
    total = len(run_records)
    passed = len([r for r in run_records if isinstance(r, dict) and str(r.get("status", "")).lower() == "passed"])
    failed = len([r for r in run_records if isinstance(r, dict) and str(r.get("status", "")).lower() == "failed"])
    blocked = len([r for r in run_records if isinstance(r, dict) and str(r.get("status", "")).lower() == "blocked"])
    success_rate = round((passed / total) * 100.0, 1) if total > 0 else 0.0
    evidence_ids = sorted(
        {
            str(eid).strip()
            for row in run_records
            if isinstance(row, dict)
            for eid in (row.get("evidence_ids") or [])
            if str(eid).strip()
        }
    )

    return {
        "report_id": f"camp-report-{uuid4().hex[:12]}",
        "generated_at": _now_iso(),
        "campaign_id": campaign.get("campaign_id"),
        "release_id": campaign.get("release_id"),
        "jurisdiction_profile": campaign.get("jurisdiction_profile"),
        "summary": {
            "total_runs": total,
            "passed_runs": passed,
            "failed_runs": failed,
            "blocked_runs": blocked,
            "success_rate_pct": success_rate,
            "independent_review_ready": bool(total > 0 and failed == 0 and blocked == 0),
        },
        "run_records": run_records,
        "evidence_export": {
            "campaign_evidence_ids": evidence_ids,
            "compliance_export_snapshot": compliance_export,
        },
        "signature": campaign.get("report_signature"),
    }


__all__ = [
    "create_campaign",
    "append_campaign_run",
    "sign_campaign_report",
    "build_campaign_report",
]
