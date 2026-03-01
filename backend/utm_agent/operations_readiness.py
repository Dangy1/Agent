from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _artifact_path(playbooks_dir: Path, artifact_id: str) -> Path:
    return playbooks_dir / f"{str(artifact_id).strip().lower()}.md"


def build_incident_playbook_index(*, required_artifacts: List[str], playbooks_dir: Path) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for artifact in required_artifacts:
        aid = str(artifact or "").strip()
        if not aid:
            continue
        path = _artifact_path(playbooks_dir, aid)
        items.append(
            {
                "artifact_id": aid,
                "path": str(path),
                "present": path.exists(),
            }
        )
    missing = [row["artifact_id"] for row in items if row.get("present") is not True]
    return {
        "required_count": len(items),
        "present_count": len([x for x in items if x.get("present") is True]),
        "missing_artifacts": missing,
        "items": items,
    }


def evaluate_slo_status(*, continuity: Dict[str, Any], observed_metrics: Dict[str, Any]) -> Dict[str, Any]:
    availability_target = float(continuity.get("availability_slo_pct", 0.0) or 0.0)
    latency_target = float(continuity.get("decision_latency_ms_p95", 0.0) or 0.0)
    rto_target = float(continuity.get("recovery_time_objective_min", 0.0) or 0.0)

    availability_observed = observed_metrics.get("availability_slo_pct")
    latency_observed = observed_metrics.get("decision_latency_ms_p95")
    rto_observed = observed_metrics.get("recovery_time_objective_min")

    availability_ok = (
        availability_observed is not None
        and float(availability_observed) >= availability_target
    )
    latency_ok = (
        latency_observed is not None
        and float(latency_observed) <= latency_target
    )
    rto_ok = (
        rto_observed is not None
        and float(rto_observed) <= rto_target
    )

    return {
        "targets": {
            "availability_slo_pct": availability_target,
            "decision_latency_ms_p95": latency_target,
            "recovery_time_objective_min": rto_target,
            "degraded_mode_required": bool(continuity.get("degraded_mode_required", False)),
        },
        "observed": {
            "availability_slo_pct": availability_observed,
            "decision_latency_ms_p95": latency_observed,
            "recovery_time_objective_min": rto_observed,
        },
        "checks": {
            "availability_ok": availability_ok,
            "latency_ok": latency_ok,
            "rto_ok": rto_ok,
        },
        "all_met": bool(availability_ok and latency_ok and rto_ok),
    }


def evaluate_operations_readiness(
    *,
    profile: Dict[str, Any],
    playbooks_dir: Path,
    observed_metrics: Dict[str, Any] | None,
    latest_conformance: Dict[str, Any] | None,
) -> Dict[str, Any]:
    continuity = dict(profile.get("continuity") or {}) if isinstance(profile.get("continuity"), dict) else {}
    incident = dict(profile.get("incident_process") or {}) if isinstance(profile.get("incident_process"), dict) else {}
    required_artifacts = [str(x).strip() for x in (incident.get("required_artifacts") or []) if str(x).strip()]

    playbook_index = build_incident_playbook_index(
        required_artifacts=required_artifacts,
        playbooks_dir=playbooks_dir,
    )
    slo_status = evaluate_slo_status(
        continuity=continuity,
        observed_metrics=dict(observed_metrics or {}),
    )
    conformance_passed = bool(isinstance(latest_conformance, dict) and latest_conformance.get("passed") is True)
    playbooks_complete = len(playbook_index.get("missing_artifacts") or []) == 0

    overall_ready = bool(conformance_passed and playbooks_complete and slo_status.get("all_met") is True)
    return {
        "generated_at": _now_iso(),
        "profile_id": str(profile.get("profile_id", "")),
        "conformance_passed": conformance_passed,
        "playbooks": playbook_index,
        "slo": slo_status,
        "operations_ready": overall_ready,
    }


__all__ = [
    "build_incident_playbook_index",
    "evaluate_slo_status",
    "evaluate_operations_readiness",
]
