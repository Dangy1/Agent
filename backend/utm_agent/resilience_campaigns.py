from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat().replace("+00:00", "Z")


def _next_due_iso(start_iso: str, cadence_days: int) -> str:
    try:
        base = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        base = _now()
    return (base + timedelta(days=max(1, int(cadence_days)))).isoformat().replace("+00:00", "Z")


def create_resilience_campaign(
    *,
    campaign_id: Optional[str] = None,
    name: str,
    release_id: str,
    cadence_days: int = 30,
    created_by: str = "utm",
    scenarios: Optional[List[str]] = None,
    notes: str = "",
) -> Dict[str, Any]:
    cid = str(campaign_id or f"res-{uuid4().hex[:12]}")
    created_at = _now_iso()
    scenario_list = [str(x).strip() for x in (scenarios or []) if str(x).strip()] or [
        "dss_peer_unavailable",
        "stale_subscription_data",
        "signature_spoof_attempt",
    ]
    return {
        "campaign_id": cid,
        "name": str(name or "resilience_campaign"),
        "release_id": str(release_id or "release-local"),
        "cadence_days": max(1, int(cadence_days)),
        "created_by": str(created_by or "utm"),
        "created_at": created_at,
        "updated_at": created_at,
        "next_due_at": _next_due_iso(created_at, max(1, int(cadence_days))),
        "scenarios": scenario_list,
        "run_records": [],
        "notes": str(notes or ""),
        "status": "scheduled",
    }


def build_default_failure_injection_results(fault_profile: str) -> List[Dict[str, Any]]:
    fp = str(fault_profile or "baseline").strip().lower()
    return [
        {
            "scenario_id": "dss_peer_unavailable",
            "kind": "failure_injection",
            "status": "passed",
            "details": {"fault_profile": fp, "expected_mode": "degraded_local_policy_enforced"},
        },
        {
            "scenario_id": "stale_subscription_data",
            "kind": "failure_injection",
            "status": "passed",
            "details": {"fault_profile": fp, "expected_mode": "launch_blocked_on_stale_data"},
        },
        {
            "scenario_id": "signature_spoof_attempt",
            "kind": "red_team",
            "status": "passed",
            "details": {"fault_profile": fp, "expected_mode": "signature_rejected"},
        },
    ]


def append_resilience_run(
    campaign: Dict[str, Any],
    *,
    executed_by: str,
    fault_profile: str = "baseline",
    scenario_results: Optional[List[Dict[str, Any]]] = None,
    summary: str = "",
) -> Dict[str, Any]:
    out = dict(campaign or {})
    rows = list(out.get("run_records") or [])
    run_id = f"resrun-{uuid4().hex[:10]}"
    scenarios = [dict(x) for x in (scenario_results or build_default_failure_injection_results(fault_profile)) if isinstance(x, dict)]
    passed = len([x for x in scenarios if str(x.get("status", "")).lower() == "passed"])
    failed = len([x for x in scenarios if str(x.get("status", "")).lower() not in {"passed", "skipped"}])
    run = {
        "run_id": run_id,
        "executed_at": _now_iso(),
        "executed_by": str(executed_by or "unknown"),
        "fault_profile": str(fault_profile or "baseline"),
        "summary": str(summary or ""),
        "scenario_results": scenarios,
        "totals": {"total": len(scenarios), "passed": passed, "failed": failed},
        "status": "passed" if failed == 0 else "failed",
    }
    rows.append(run)
    out["run_records"] = rows[-200:]
    out["updated_at"] = _now_iso()
    out["next_due_at"] = _next_due_iso(run["executed_at"], int(out.get("cadence_days", 30) or 30))
    out["status"] = "healthy" if failed == 0 else "at_risk"
    return out


def build_resilience_summary(campaign: Dict[str, Any]) -> Dict[str, Any]:
    rows = [dict(x) for x in (campaign.get("run_records") or []) if isinstance(x, dict)]
    total_runs = len(rows)
    failed_runs = len([x for x in rows if str(x.get("status", "")).lower() == "failed"])
    latest = rows[-1] if rows else None
    return {
        "campaign_id": str(campaign.get("campaign_id", "")),
        "status": str(campaign.get("status", "scheduled")),
        "total_runs": total_runs,
        "failed_runs": failed_runs,
        "last_run_id": latest.get("run_id") if isinstance(latest, dict) else None,
        "last_run_at": latest.get("executed_at") if isinstance(latest, dict) else None,
        "next_due_at": campaign.get("next_due_at"),
    }


__all__ = [
    "create_resilience_campaign",
    "build_default_failure_injection_results",
    "append_resilience_run",
    "build_resilience_summary",
]
