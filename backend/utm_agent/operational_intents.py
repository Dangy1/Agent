from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4


ACTIVE_INTENT_STATES = {"accepted", "activated", "contingent", "nonconforming"}
INTENT_PRIORITIES = {"emergency": 0, "high": 1, "normal": 2, "low": 3}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _interval_from_volume(volume4d: Dict[str, Any], key: str, *, default: tuple[float, float]) -> tuple[float, float]:
    direct = volume4d.get(key)
    if isinstance(direct, (list, tuple)) and len(direct) >= 2:
        lo = float(direct[0])
        hi = float(direct[1])
        return (min(lo, hi), max(lo, hi))
    lo = volume4d.get(f"{key}_min")
    hi = volume4d.get(f"{key}_max")
    if lo is None or hi is None:
        box = volume4d.get("bounds")
        if isinstance(box, dict):
            lo = box.get(f"{key}_min", lo)
            hi = box.get(f"{key}_max", hi)
    if lo is None or hi is None:
        return default
    lo_f = float(lo)
    hi_f = float(hi)
    return (min(lo_f, hi_f), max(lo_f, hi_f))


def normalize_volume4d(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("volume4d must be an object")
    x = _interval_from_volume(raw, "x", default=(-1e9, 1e9))
    y = _interval_from_volume(raw, "y", default=(-1e9, 1e9))
    z = _interval_from_volume(raw, "z", default=(0.0, 120.0))

    start_dt = _parse_dt(raw.get("time_start")) or _parse_dt(raw.get("start_time"))
    end_dt = _parse_dt(raw.get("time_end")) or _parse_dt(raw.get("end_time"))
    if start_dt is None:
        start_dt = datetime.now(timezone.utc)
    if end_dt is None:
        end_dt = start_dt + timedelta(minutes=20)
    if end_dt <= start_dt:
        raise ValueError("volume4d time_end must be after time_start")

    return {
        "x": [x[0], x[1]],
        "y": [y[0], y[1]],
        "z": [z[0], z[1]],
        "time_start": start_dt.isoformat().replace("+00:00", "Z"),
        "time_end": end_dt.isoformat().replace("+00:00", "Z"),
    }


def volume4d_overlaps(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    try:
        aa = normalize_volume4d(a)
        bb = normalize_volume4d(b)
    except Exception:
        return False
    if aa["x"][1] < bb["x"][0] or bb["x"][1] < aa["x"][0]:
        return False
    if aa["y"][1] < bb["y"][0] or bb["y"][1] < aa["y"][0]:
        return False
    if aa["z"][1] < bb["z"][0] or bb["z"][1] < aa["z"][0]:
        return False
    a_start = _parse_dt(aa["time_start"])
    a_end = _parse_dt(aa["time_end"])
    b_start = _parse_dt(bb["time_start"])
    b_end = _parse_dt(bb["time_end"])
    if not (a_start and a_end and b_start and b_end):
        return False
    return not (a_end < b_start or b_end < a_start)


def _priority_rank(value: Any) -> int:
    return INTENT_PRIORITIES.get(str(value or "normal").strip().lower(), INTENT_PRIORITIES["normal"])


def _state_value(value: Any) -> str:
    v = str(value or "accepted").strip().lower()
    return v if v else "accepted"


def _priority_value(value: Any) -> str:
    v = str(value or "normal").strip().lower()
    return v if v in INTENT_PRIORITIES else "normal"


def _is_active_intent(intent: Dict[str, Any]) -> bool:
    return _state_value(intent.get("state")) in ACTIVE_INTENT_STATES


def build_conflicts(candidate: Dict[str, Any], others: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    c_priority = _priority_rank(candidate.get("priority"))
    c_manager = str(candidate.get("manager_uss_id") or "")
    c_id = str(candidate.get("intent_id") or "")
    c_volume = candidate.get("volume4d")
    if not isinstance(c_volume, dict):
        return conflicts

    for other in others:
        if not isinstance(other, dict):
            continue
        o_id = str(other.get("intent_id") or "")
        if not o_id or o_id == c_id:
            continue
        if not _is_active_intent(other):
            continue
        o_volume = other.get("volume4d")
        if not isinstance(o_volume, dict):
            continue
        if not volume4d_overlaps(c_volume, o_volume):
            continue

        o_priority = _priority_rank(other.get("priority"))
        o_manager = str(other.get("manager_uss_id") or "")

        if c_manager and o_manager and c_manager == o_manager:
            severity = "self_overlap"
            blocking = False
        elif c_priority > o_priority:
            severity = "blocking"
            blocking = True
        elif c_priority == o_priority:
            severity = "blocking"
            blocking = True
        else:
            severity = "advisory"
            blocking = False

        conflicts.append(
            {
                "intent_id": o_id,
                "manager_uss_id": o_manager,
                "state": _state_value(other.get("state")),
                "priority": _priority_value(other.get("priority")),
                "blocking": blocking,
                "severity": severity,
                "reason": "4d_volume_overlap",
            }
        )
    return conflicts


def _sanitize_metadata(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def upsert_intent(
    intents: Dict[str, Dict[str, Any]],
    *,
    intent_id: str | None = None,
    manager_uss_id: str = "uss-local",
    state: str = "accepted",
    priority: str = "normal",
    volume4d: Dict[str, Any],
    conflict_policy: str = "reject",
    ovn: str | None = None,
    uss_base_url: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    iid = str(intent_id or f"oi-{uuid4().hex[:12]}")
    prev = intents.get(iid) if isinstance(intents.get(iid), dict) else None
    version = int(prev.get("version", 0) or 0) + 1 if prev else 1
    policy = str(conflict_policy or "reject").strip().lower()
    if policy not in {"reject", "negotiate", "conditional_approve"}:
        policy = "reject"

    record = {
        "intent_id": iid,
        "manager_uss_id": str(manager_uss_id or (prev.get("manager_uss_id") if prev else "uss-local")),
        "state": _state_value(state if state is not None else (prev.get("state") if prev else "accepted")),
        "priority": _priority_value(priority if priority is not None else (prev.get("priority") if prev else "normal")),
        "volume4d": normalize_volume4d(volume4d),
        "ovn": str(ovn or f"ovn-{uuid4().hex[:10]}"),
        "version": version,
        "uss_base_url": str(uss_base_url or (prev.get("uss_base_url") if prev else "") or ""),
        "metadata": _sanitize_metadata(metadata if metadata is not None else (prev.get("metadata") if prev else {})),
        "updated_at": _now_iso(),
    }

    other_intents = [dict(v) for k, v in intents.items() if k != iid and isinstance(v, dict)]
    conflicts = build_conflicts(record, other_intents)
    blocking_conflicts = [c for c in conflicts if c.get("blocking") is True]
    conflict_summary = {
        "total": len(conflicts),
        "blocking": len(blocking_conflicts),
        "advisory": len([c for c in conflicts if c.get("severity") == "advisory"]),
        "self_overlap": len([c for c in conflicts if c.get("severity") == "self_overlap"]),
    }
    record["conflicts"] = conflicts
    record["conflict_summary"] = conflict_summary

    rejected = bool(blocking_conflicts and policy == "reject")
    if not rejected:
        intents[iid] = record

    return {
        "status": "rejected" if rejected else "success",
        "conflict_policy": policy,
        "blocking_conflicts": blocking_conflicts,
        "intent": record,
        "stored": not rejected,
    }


def delete_intent(intents: Dict[str, Dict[str, Any]], intent_id: str) -> Dict[str, Any]:
    iid = str(intent_id or "").strip()
    if not iid:
        return {"deleted": False, "error": "intent_id_required"}
    existed = intents.pop(iid, None)
    return {"deleted": existed is not None, "intent_id": iid, "intent": dict(existed) if isinstance(existed, dict) else None}


def query_intents(
    intents: Dict[str, Dict[str, Any]],
    *,
    manager_uss_id: str | None = None,
    states: List[str] | None = None,
    volume4d: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    state_set = {str(s).strip().lower() for s in (states or []) if str(s).strip()}
    query_volume = normalize_volume4d(volume4d) if isinstance(volume4d, dict) else None
    out: List[Dict[str, Any]] = []
    for value in intents.values():
        if not isinstance(value, dict):
            continue
        if manager_uss_id and str(value.get("manager_uss_id") or "") != str(manager_uss_id):
            continue
        rec_state = _state_value(value.get("state"))
        if state_set and rec_state not in state_set:
            continue
        if query_volume is not None:
            rec_volume = value.get("volume4d")
            if not isinstance(rec_volume, dict) or not volume4d_overlaps(query_volume, rec_volume):
                continue
        out.append(dict(value))
    out.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return out

