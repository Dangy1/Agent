from typing import Any, Dict, List, Optional


def _require_int(value: Any, field: str) -> Optional[str]:
    if isinstance(value, bool):
        return f"{field} must be an integer, not boolean"
    try:
        int(value)
    except Exception:
        return f"{field} must be an integer"
    return None


def _require_number(value: Any, field: str) -> Optional[str]:
    if isinstance(value, bool):
        return f"{field} must be a number, not boolean"
    try:
        float(value)
    except Exception:
        return f"{field} must be a number"
    return None


def _validate_slice_config(config: Any) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(config, dict):
        return {"ok": False, "errors": ["config must be a JSON object"], "warnings": warnings}

    algo = str(config.get("slice_sched_algo", "")).strip().upper()
    if algo not in {"STATIC", "NVS", "EDF"}:
        errors.append("config.slice_sched_algo must be one of: STATIC, NVS, EDF")

    slices = config.get("slices")
    if not isinstance(slices, list) or len(slices) == 0:
        errors.append("config.slices must be a non-empty array")
        return {"ok": False, "errors": errors, "warnings": warnings}

    seen_ids: set[int] = set()
    static_ranges: List[tuple[int, int, int]] = []
    nvs_cap_sum = 0.0

    for i, sl in enumerate(slices):
        path = f"config.slices[{i}]"
        if not isinstance(sl, dict):
            errors.append(f"{path} must be an object")
            continue

        id_err = _require_int(sl.get("id"), f"{path}.id")
        if id_err:
            errors.append(id_err)
        else:
            sid = int(sl["id"])
            if sid < 0:
                errors.append(f"{path}.id must be >= 0")
            if sid in seen_ids:
                errors.append(f"{path}.id duplicates slice id {sid}")
            seen_ids.add(sid)

        label = sl.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{path}.label must be a non-empty string")

        params = sl.get("slice_algo_params")
        if not isinstance(params, dict):
            errors.append(f"{path}.slice_algo_params must be an object")
            continue

        if algo == "STATIC":
            for k in ("pos_low", "pos_high"):
                err = _require_int(params.get(k), f"{path}.slice_algo_params.{k}")
                if err:
                    errors.append(err)
            if all(_require_int(params.get(k), k) is None for k in ("pos_low", "pos_high")):
                lo, hi = int(params["pos_low"]), int(params["pos_high"])
                if lo < 0 or hi < 0:
                    errors.append(f"{path}.slice_algo_params.pos_low/pos_high must be >= 0")
                if lo > hi:
                    errors.append(f"{path}.slice_algo_params.pos_low must be <= pos_high")
                static_ranges.append((lo, hi, i))

        elif algo == "NVS":
            nvs_type = str(sl.get("type", "RATE")).strip().upper()
            if nvs_type in {"SLICE_SM_NVS_V0_RATE", "RATE"}:
                for k in ("mbps_rsvd", "mbps_ref"):
                    err = _require_int(params.get(k), f"{path}.slice_algo_params.{k}")
                    if err:
                        errors.append(err)
                if all(_require_int(params.get(k), k) is None for k in ("mbps_rsvd", "mbps_ref")):
                    mbps_rsvd = int(params["mbps_rsvd"])
                    mbps_ref = int(params["mbps_ref"])
                    if mbps_rsvd <= 0 or mbps_ref <= 0:
                        errors.append(f"{path}.slice_algo_params.mbps_rsvd/mbps_ref must be > 0")
                    if mbps_rsvd > mbps_ref:
                        errors.append(f"{path}.slice_algo_params.mbps_rsvd must be <= mbps_ref")
            elif nvs_type in {"SLICE_SM_NVS_V0_CAPACITY", "CAPACITY"}:
                err = _require_number(params.get("pct_rsvd"), f"{path}.slice_algo_params.pct_rsvd")
                if err:
                    errors.append(err)
                else:
                    pct = float(params["pct_rsvd"])
                    if pct <= 0 or pct > 1:
                        errors.append(f"{path}.slice_algo_params.pct_rsvd must be in (0, 1]")
                    nvs_cap_sum += pct
            else:
                errors.append(f"{path}.type must be RATE or CAPACITY for NVS")

        elif algo == "EDF":
            for k in ("deadline", "guaranteed_prbs", "max_replenish"):
                err = _require_int(params.get(k), f"{path}.slice_algo_params.{k}")
                if err:
                    errors.append(err)
            if all(_require_int(params.get(k), k) is None for k in ("deadline", "guaranteed_prbs", "max_replenish")):
                deadline = int(params["deadline"])
                guaranteed_prbs = int(params["guaranteed_prbs"])
                max_replenish = int(params["max_replenish"])
                if deadline <= 0:
                    errors.append(f"{path}.slice_algo_params.deadline must be > 0")
                if guaranteed_prbs < 0:
                    errors.append(f"{path}.slice_algo_params.guaranteed_prbs must be >= 0")
                if max_replenish < 0:
                    errors.append(f"{path}.slice_algo_params.max_replenish must be >= 0")

    if algo == "STATIC":
        ordered = sorted(static_ranges, key=lambda t: t[0])
        for idx in range(1, len(ordered)):
            prev_lo, prev_hi, prev_i = ordered[idx - 1]
            cur_lo, cur_hi, cur_i = ordered[idx]
            if cur_lo <= prev_hi:
                warnings.append(
                    f"STATIC ranges overlap between slices[{prev_i}] ({prev_lo}-{prev_hi}) and slices[{cur_i}] ({cur_lo}-{cur_hi})"
                )

    if algo == "NVS" and nvs_cap_sum > 1.0 + 1e-9:
        errors.append(f"Sum of NVS CAPACITY pct_rsvd values must be <= 1.0 (got {nvs_cap_sum:.3f})")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def _validate_slice_profile(profile: str) -> Optional[str]:
    allowed = {"monitor", "static", "nvs-rate", "nvs-cap", "edf", "all"}
    if profile not in allowed:
        return f"Invalid slice profile '{profile}'. Allowed: {', '.join(sorted(allowed))}."
    return None


def _validate_int_range(name: str, value: int, min_v: int, max_v: int) -> Optional[str]:
    if value < min_v or value > max_v:
        return f"{name}={value} is out of range [{min_v}, {max_v}]."
    return None


def _clamp_int(value: int, min_v: int, max_v: int) -> int:
    return max(min_v, min(max_v, value))


def _looks_like_kpm_line(line: str) -> bool:
    s = line.lower()
    if "meas=" in s:
        return True
    return ("kpm" in s and ("indication" in s or "metric" in s or "rru" in s or "ue" in s))


def _extract_kpm_indication_lines(lines: List[str], max_items: int = 12) -> List[str]:
    picked: List[str] = []
    seen: set[str] = set()
    for line in reversed(lines):
        text = str(line).strip()
        if not text or not _looks_like_kpm_line(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        picked.append(text)
        if len(picked) >= max_items:
            break
    picked.reverse()
    return picked


def _validate_slice_start_inputs(profile: str, duration_s: int, assoc_dl_id: Optional[int] = None) -> Optional[str]:
    msg = _validate_slice_profile(profile)
    if msg:
        return msg
    msg = _validate_int_range("duration_s", int(duration_s), 1, 3600)
    if msg:
        return msg
    if assoc_dl_id is not None:
        msg = _validate_int_range("assoc_dl_id", int(assoc_dl_id), 0, 255)
        if msg:
            return msg
    return None


def _validate_slice_verify_inputs(
    profile: str,
    duration_s: int,
    startup_timeout_s: int,
    verify_tail_lines: int,
    assoc_dl_id: Optional[int] = None,
) -> Optional[str]:
    msg = _validate_slice_start_inputs(profile, duration_s, assoc_dl_id=assoc_dl_id)
    if msg:
        return msg
    msg = _validate_int_range("startup_timeout_s", int(startup_timeout_s), 1, 120)
    if msg:
        return msg
    msg = _validate_int_range("verify_tail_lines", int(verify_tail_lines), 20, 1000)
    if msg:
        return msg
    return None
