from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


ACTIVE_DSS_INTENT_STATES = {"accepted", "activated", "contingent", "nonconforming"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_utc_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _is_local_uss(manager_uss_id: str) -> bool:
    raw = str(manager_uss_id or "").strip().lower()
    return raw.startswith("uss-local")


def _extract_uav_id(intent: Dict[str, Any]) -> str:
    metadata = intent.get("metadata") if isinstance(intent.get("metadata"), dict) else {}
    for key in ("uav_id", "uavId"):
        value = str(metadata.get(key, "")).strip()
        if value:
            return value
    intent_id = str(intent.get("intent_id", "")).strip()
    if ":" in intent_id:
        parts = intent_id.split(":")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    return ""


def _blocking_conflict_count(intent: Dict[str, Any]) -> int:
    summary = intent.get("conflict_summary") if isinstance(intent.get("conflict_summary"), dict) else {}
    if isinstance(summary, dict):
        return max(0, _safe_int(summary.get("blocking"), 0))
    conflicts = intent.get("conflicts") if isinstance(intent.get("conflicts"), list) else []
    count = 0
    for rec in conflicts:
        if isinstance(rec, dict) and rec.get("blocking") is True:
            count += 1
    return count


def _bool_or_none(value: Any) -> bool | None:
    if value is True:
        return True
    if value is False:
        return False
    return None


def _notifications_by_intent(notifications: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for rec in notifications:
        if not isinstance(rec, dict):
            continue
        intent_id = str(rec.get("source_intent_id", "")).strip()
        if not intent_id:
            continue
        row = out.setdefault(
            intent_id,
            {
                "total": 0,
                "pending": 0,
                "delivered": 0,
                "failed": 0,
                "acked": 0,
                "last_status": "",
                "last_created_at": "",
            },
        )
        row["total"] = _safe_int(row.get("total"), 0) + 1
        status = str(rec.get("status", "")).strip().lower()
        if status in {"pending", "delivered", "failed", "acked"}:
            row[status] = _safe_int(row.get(status), 0) + 1
        created_at = str(rec.get("created_at", "")).strip()
        if _to_utc_dt(created_at) >= _to_utc_dt(row.get("last_created_at")):
            row["last_created_at"] = created_at
            row["last_status"] = status
    return out


def _latest_intent_by_uav(operational_intents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for intent in operational_intents:
        if not isinstance(intent, dict):
            continue
        uav_id = _extract_uav_id(intent)
        if not uav_id:
            continue
        prev = out.get(uav_id)
        if not isinstance(prev, dict):
            out[uav_id] = dict(intent)
            continue
        prev_ts = _to_utc_dt(prev.get("updated_at") or prev.get("time_end"))
        cur_ts = _to_utc_dt(intent.get("updated_at") or intent.get("time_end"))
        if cur_ts >= prev_ts:
            out[uav_id] = dict(intent)
    return out


def build_layered_status(
    *,
    airspace_segment: str,
    fleet: Dict[str, Dict[str, Any]],
    operational_intents: List[Dict[str, Any]],
    subscriptions: List[Dict[str, Any]],
    participants: List[Dict[str, Any]],
    notifications: List[Dict[str, Any]],
    weather_check: Dict[str, Any] | None = None,
    intents_adapter_mode: Any = None,
    intents_degraded: Any = None,
    intents_failover_reason: Any = None,
    subscriptions_adapter_mode: Any = None,
    subscriptions_degraded: Any = None,
    subscriptions_failover_reason: Any = None,
) -> Dict[str, Any]:
    participants_by_id: Dict[str, Dict[str, Any]] = {}
    for row in participants:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("participant_id", "")).strip()
        if pid:
            participants_by_id[pid] = dict(row)

    intent_by_uav = _latest_intent_by_uav(operational_intents)
    notif_by_intent = _notifications_by_intent(notifications)

    # Per-UAV cards should represent currently active fleet members.
    # Intents that reference deleted/unknown UAVs are tracked separately as orphaned.
    uav_ids = sorted([str(k) for k in fleet.keys()])
    orphaned_intent_uav_ids = sorted([uav_id for uav_id in intent_by_uav.keys() if uav_id not in fleet])
    cards: List[Dict[str, Any]] = []
    summary_counts = {"ready": 0, "attention": 0, "blocked": 0, "pending": 0}

    for uav_id in uav_ids:
        snap = fleet.get(uav_id) if isinstance(fleet.get(uav_id), dict) else {}
        intent = intent_by_uav.get(uav_id) if isinstance(intent_by_uav.get(uav_id), dict) else {}

        approval = snap.get("utm_approval") if isinstance(snap.get("utm_approval"), dict) else {}
        geofence = snap.get("utm_geofence_result") if isinstance(snap.get("utm_geofence_result"), dict) else {}
        dss_result = snap.get("utm_dss_result") if isinstance(snap.get("utm_dss_result"), dict) else {}

        approval_granted = _bool_or_none(approval.get("approved") if isinstance(approval, dict) else None)
        raw_geofence_ok = geofence.get("ok") if isinstance(geofence, dict) else None
        if not isinstance(raw_geofence_ok, bool):
            raw_geofence_ok = geofence.get("geofence_ok") if isinstance(geofence, dict) else None
        geofence_ok = _bool_or_none(raw_geofence_ok)

        manager_uss_id = str(intent.get("manager_uss_id", "")).strip() if isinstance(intent, dict) else ""
        participant = participants_by_id.get(manager_uss_id) if manager_uss_id else None
        participant_known = bool(participant) or _is_local_uss(manager_uss_id)
        participant_active = (
            str((participant or {}).get("status", "")).strip().lower() == "active"
            if isinstance(participant, dict)
            else _is_local_uss(manager_uss_id)
        )

        intent_id = str(intent.get("intent_id", "")).strip() if isinstance(intent, dict) else ""
        intent_state = str(intent.get("state", "none")).strip().lower() if isinstance(intent, dict) else "none"
        intent_blocking = _blocking_conflict_count(intent) if isinstance(intent, dict) else 0

        dss_blocking = 0
        blocking_conflicts = dss_result.get("blocking_conflicts") if isinstance(dss_result, dict) else None
        if isinstance(blocking_conflicts, list):
            dss_blocking = len(blocking_conflicts)
        dss_blocking = max(intent_blocking, dss_blocking)

        dss_status = str(dss_result.get("status", "")).strip().lower() if isinstance(dss_result, dict) else ""
        dss_error = str(dss_result.get("error") or dss_result.get("details") or "").strip() if isinstance(dss_result, dict) else ""
        dss_degraded = bool(dss_result.get("degraded")) if isinstance(dss_result, dict) else bool(intents_degraded or subscriptions_degraded)

        notif = notif_by_intent.get(intent_id, {})
        notif_pending = _safe_int(notif.get("pending"), 0)

        issues: List[str] = []
        if approval_granted is False:
            issues.append("utm_approval_denied")
        elif approval_granted is None:
            issues.append("utm_approval_missing")
        if geofence_ok is False:
            issues.append("utm_geofence_or_nfz_failed")
        if not intent_id:
            issues.append("dss_operational_intent_missing")
        if dss_blocking > 0:
            issues.append(f"dss_blocking_conflicts:{dss_blocking}")
        if dss_status == "error" or dss_error:
            issues.append(f"dss_publication_error:{dss_error or dss_status}")
        if manager_uss_id and not participant_known:
            issues.append(f"uss_manager_unregistered:{manager_uss_id}")
        if manager_uss_id and not participant_active:
            issues.append(f"uss_manager_inactive:{manager_uss_id}")
        if notif_pending > 0:
            issues.append(f"dss_notifications_pending:{notif_pending}")
        if dss_degraded:
            issues.append("dss_degraded_mode")

        has_blocking = any(x.startswith("dss_blocking_conflicts:") or x.startswith("dss_publication_error:") for x in issues)
        if has_blocking:
            overall_status = "blocked"
        elif approval_granted is True and geofence_ok is not False and intent_id and not dss_degraded:
            overall_status = "ready"
        elif approval_granted is False or geofence_ok is False or manager_uss_id and not participant_active:
            overall_status = "attention"
        else:
            overall_status = "pending"
        summary_counts[overall_status] = _safe_int(summary_counts.get(overall_status), 0) + 1

        cards.append(
            {
                "uav_id": uav_id,
                "airspace_segment": airspace_segment,
                "overall_status": overall_status,
                "issues": issues,
                "flight_phase": str(snap.get("flight_phase", "")) if isinstance(snap, dict) else "",
                "armed": _bool_or_none(snap.get("armed") if isinstance(snap, dict) else None),
                "active": _bool_or_none(snap.get("active") if isinstance(snap, dict) else None),
                "utm_layer": {
                    "approval_granted": approval_granted,
                    "geofence_ok": geofence_ok,
                    "approval_reason": str(approval.get("reason", "")) if isinstance(approval, dict) else "",
                },
                "uss_layer": {
                    "manager_uss_id": manager_uss_id,
                    "participant_known": participant_known if manager_uss_id else None,
                    "participant_active": participant_active if manager_uss_id else None,
                    "participant_status": str((participant or {}).get("status", "")) if isinstance(participant, dict) else "",
                },
                "dss_layer": {
                    "intent_id": intent_id,
                    "intent_state": intent_state,
                    "blocking_conflicts": dss_blocking,
                    "intent_active": intent_state in ACTIVE_DSS_INTENT_STATES,
                    "publication_status": dss_status or ("success" if intent_id else ""),
                    "publication_error": dss_error,
                    "degraded": dss_degraded,
                    "adapter_mode": (
                        str(dss_result.get("adapter_mode", "")).strip()
                        if isinstance(dss_result, dict)
                        else str(intents_adapter_mode or "")
                    ),
                    "pending_notifications": notif_pending,
                    "last_notification_status": str(notif.get("last_status", "")),
                },
            }
        )

    managers_seen = {
        str(intent.get("manager_uss_id", "")).strip()
        for intent in operational_intents
        if isinstance(intent, dict) and str(intent.get("manager_uss_id", "")).strip()
    }
    unknown_managers = [m for m in managers_seen if not _is_local_uss(m) and m not in participants_by_id]

    weather_ok = weather_check.get("ok") if isinstance(weather_check, dict) else None
    approved_count = len(
        [
            1
            for snap in fleet.values()
            if isinstance(snap, dict)
            and isinstance(snap.get("utm_approval"), dict)
            and snap.get("utm_approval", {}).get("approved") is True
        ]
    )
    geofence_ok_count = len(
        [
            1
            for snap in fleet.values()
            if isinstance(snap, dict)
            and isinstance(snap.get("utm_geofence_result"), dict)
            and (
                snap.get("utm_geofence_result", {}).get("ok") is True
                or snap.get("utm_geofence_result", {}).get("geofence_ok") is True
            )
        ]
    )

    active_intents = [
        intent
        for intent in operational_intents
        if isinstance(intent, dict) and str(intent.get("state", "")).strip().lower() in ACTIVE_DSS_INTENT_STATES
    ]
    blocking_intents = [intent for intent in active_intents if _blocking_conflict_count(intent) > 0]
    pending_notifications = [n for n in notifications if isinstance(n, dict) and str(n.get("status", "")).strip().lower() == "pending"]
    failed_notifications = [n for n in notifications if isinstance(n, dict) and str(n.get("status", "")).strip().lower() == "failed"]

    layer_utm_ok = bool(weather_ok is not False and _safe_int(summary_counts.get("blocked"), 0) == 0)
    layer_uss_ok = len(unknown_managers) == 0
    layer_dss_ok = len(blocking_intents) == 0 and len(failed_notifications) == 0 and not bool(intents_degraded or subscriptions_degraded)

    return {
        "generated_at": _now_iso(),
        "spec_mapping": {
            "intent_fields": ["intent_id", "manager_uss_id", "state", "conflict_summary.blocking", "metadata.uav_id"],
            "notification_fields": ["source_intent_id", "status", "created_at"],
            "uav_fields": ["utm_approval.approved", "utm_geofence_result.ok", "flight_phase", "armed", "active"],
            "layer_roles": {
                "utm": "policy and safety decision layer",
                "uss": "operator/participant service layer",
                "dss": "shared intent and notification exchange layer",
            },
        },
        "layers": {
            "utm": {
                "ok": layer_utm_ok,
                "weather_ok": weather_ok,
                "fleet_count": len(fleet),
                "approved_uav_count": approved_count,
                "geofence_ok_uav_count": geofence_ok_count,
                "blocked_uav_count": _safe_int(summary_counts.get("blocked"), 0),
            },
            "uss": {
                "ok": layer_uss_ok,
                "participant_count": len(participants),
                "active_participant_count": len([p for p in participants if isinstance(p, dict) and str(p.get("status", "")).strip().lower() == "active"]),
                "manager_uss_seen_count": len(managers_seen),
                "unknown_manager_count": len(unknown_managers),
                "unknown_manager_ids": sorted(unknown_managers),
            },
            "dss": {
                "ok": layer_dss_ok,
                "intent_count": len(operational_intents),
                "active_intent_count": len(active_intents),
                "blocking_intent_count": len(blocking_intents),
                "orphaned_intent_uav_count": len(orphaned_intent_uav_ids),
                "orphaned_intent_uav_ids": orphaned_intent_uav_ids,
                "subscription_count": len(subscriptions),
                "notification_count": len(notifications),
                "pending_notification_count": len(pending_notifications),
                "failed_notification_count": len(failed_notifications),
                "intents_adapter_mode": intents_adapter_mode,
                "intents_degraded": bool(intents_degraded),
                "intents_failover_reason": str(intents_failover_reason or ""),
                "subscriptions_adapter_mode": subscriptions_adapter_mode,
                "subscriptions_degraded": bool(subscriptions_degraded),
                "subscriptions_failover_reason": str(subscriptions_failover_reason or ""),
            },
        },
        "summary": {
            "fleet_count": len(fleet),
            "uav_ready_count": _safe_int(summary_counts.get("ready"), 0),
            "uav_attention_count": _safe_int(summary_counts.get("attention"), 0),
            "uav_blocked_count": _safe_int(summary_counts.get("blocked"), 0),
            "uav_pending_count": _safe_int(summary_counts.get("pending"), 0),
        },
        "uav_status_cards": cards,
    }


__all__ = ["build_layered_status"]
