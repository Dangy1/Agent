"""Shared UAV copilot LLM/JSON helpers."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List

from oran_agent.config.runtime_llm import get_llm_runtime_config
from oran_agent.llm_factory import build_chat_model_from_config


_UAV_COPILOT_MODEL: Any | None = None
_UAV_COPILOT_MODEL_KEY = ""
_UAV_COPILOT_MODEL_META: Dict[str, Any] = {}


def _utm_nfz_conflict_feedback(verify_result: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(verify_result, dict):
        return {"has_conflict": False, "waypoint_indices": [], "segment_indices": [], "summary": ""}
    checks = verify_result.get("checks")
    if not isinstance(checks, dict):
        return {"has_conflict": False, "waypoint_indices": [], "segment_indices": [], "summary": ""}
    nfz = checks.get("no_fly_zone")
    if not isinstance(nfz, dict):
        return {"has_conflict": False, "waypoint_indices": [], "segment_indices": [], "summary": ""}
    wp_conflicts = nfz.get("waypoint_conflicts") if isinstance(nfz.get("waypoint_conflicts"), list) else []
    seg_conflicts = nfz.get("segment_conflicts") if isinstance(nfz.get("segment_conflicts"), list) else []
    wp_idx = sorted({int(c.get("waypoint_index")) for c in wp_conflicts if isinstance(c, dict) and isinstance(c.get("waypoint_index"), int)})
    seg_idx = sorted(
        {
            (int(c.get("segment_start_index")), int(c.get("segment_end_index")))
            for c in seg_conflicts
            if isinstance(c, dict) and isinstance(c.get("segment_start_index"), int) and isinstance(c.get("segment_end_index"), int)
        }
    )
    parts: List[str] = []
    if wp_idx:
        parts.append("waypoints " + ",".join(str(i + 1) for i in wp_idx))
    if seg_idx:
        parts.append("segments " + ",".join(f"{a + 1}-{b + 1}" for a, b in seg_idx))
    return {
        "has_conflict": bool(wp_conflicts or seg_conflicts or (isinstance(nfz.get("ok"), bool) and not nfz.get("ok"))),
        "waypoint_indices": wp_idx,
        "segment_indices": seg_idx,
        "summary": "; ".join(parts),
    }


def _json_text(value: Any, max_len: int = 20000) -> str:
    try:
        text = json.dumps(value, default=str, ensure_ascii=True)
    except Exception:
        text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _extract_first_json_object(text: str) -> Dict[str, Any] | None:
    if not text:
        return None
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(text[start:], start=start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        return obj if isinstance(obj, dict) else None
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None


def _copilot_llm_config() -> Dict[str, Any]:
    cfg = get_llm_runtime_config()
    provider_override = str(os.getenv("UAV_COPILOT_LLM_PROVIDER", "") or "").strip().lower()
    if provider_override in {"ollama", "openai", "auto"}:
        cfg["provider"] = provider_override

    # Backward-compatible UAV copilot Ollama overrides.
    if str(os.getenv("UAV_COPILOT_OLLAMA_MODEL", "") or "").strip():
        cfg["ollama_model"] = str(os.getenv("UAV_COPILOT_OLLAMA_MODEL")).strip()
    if str(os.getenv("UAV_COPILOT_OLLAMA_URL", "") or "").strip():
        cfg["ollama_url"] = str(os.getenv("UAV_COPILOT_OLLAMA_URL")).strip()

    # Optional UAV copilot OpenAI overrides.
    if str(os.getenv("UAV_COPILOT_OPENAI_MODEL", "") or "").strip():
        cfg["openai_model"] = str(os.getenv("UAV_COPILOT_OPENAI_MODEL")).strip()
    if str(os.getenv("UAV_COPILOT_OPENAI_BASE_URL", "") or "").strip():
        cfg["openai_base_url"] = str(os.getenv("UAV_COPILOT_OPENAI_BASE_URL")).strip()
    if os.getenv("UAV_COPILOT_OPENAI_API_KEY") is not None:
        cfg["openai_api_key"] = str(os.getenv("UAV_COPILOT_OPENAI_API_KEY") or "").strip()
    return cfg


def _copilot_llm_cache_key(cfg: Dict[str, Any]) -> str:
    key_fingerprint = hashlib.sha256(str(cfg.get("openai_api_key", "")).encode("utf-8")).hexdigest()[:12]
    obj = {
        "provider": str(cfg.get("provider", "")),
        "ollama_url": str(cfg.get("ollama_url", "")),
        "ollama_model": str(cfg.get("ollama_model", "")),
        "openai_model": str(cfg.get("openai_model", "")),
        "openai_base_url": str(cfg.get("openai_base_url", "")),
        "fallback_to_openai": bool(cfg.get("fallback_to_openai", True)),
        "openai_api_key_sha": key_fingerprint,
    }
    return json.dumps(obj, sort_keys=True, ensure_ascii=True)


def _copilot_model() -> tuple[Any | None, Dict[str, Any]]:
    global _UAV_COPILOT_MODEL, _UAV_COPILOT_MODEL_KEY, _UAV_COPILOT_MODEL_META
    cfg = _copilot_llm_config()
    cache_key = _copilot_llm_cache_key(cfg)
    if _UAV_COPILOT_MODEL is not None and cache_key == _UAV_COPILOT_MODEL_KEY:
        return _UAV_COPILOT_MODEL, dict(_UAV_COPILOT_MODEL_META)
    try:
        model_obj, meta = build_chat_model_from_config(cfg, temperature=0)
    except Exception as e:
        return None, {
            "provider": str(cfg.get("provider", "ollama")),
            "model": "",
            "error": str(e),
        }
    _UAV_COPILOT_MODEL = model_obj
    _UAV_COPILOT_MODEL_KEY = cache_key
    _UAV_COPILOT_MODEL_META = meta.as_dict()
    return _UAV_COPILOT_MODEL, dict(_UAV_COPILOT_MODEL_META)


def _ollama_model() -> Any | None:
    # Backward-compatible helper name used by internal modules.
    model_obj, _meta = _copilot_model()
    return model_obj


def _chat_completion_json(
    *,
    system_prompt: str,
    user_payload: Dict[str, Any],
    unavailable_error_message: str = "LLM model not available (provider not configured or dependency missing)",
) -> Dict[str, Any]:
    model_obj, model_meta = _copilot_model()
    provider = str(model_meta.get("provider", _copilot_llm_config().get("provider", "ollama")))
    model = str(model_meta.get("model", "") or "")
    config_provider = str(model_meta.get("config_provider", _copilot_llm_config().get("provider", "ollama")))
    fallback_used = bool(model_meta.get("fallback_used", False))
    if model_obj is None:
        return {
            "status": "unavailable",
            "provider": provider,
            "model": model,
            "config_provider": config_provider,
            "fallback_used": fallback_used,
            "error": model_meta.get("error") or unavailable_error_message,
        }
    try:
        resp = model_obj.invoke(
            [
                ("system", system_prompt),
                ("human", _json_text(user_payload, max_len=50000)),
            ]
        )
        content = getattr(resp, "content", None)
        text = content if isinstance(content, str) else _json_text(content)
        parsed = _extract_first_json_object(text or "")
        if not isinstance(parsed, dict):
            return {
                "status": "error",
                "provider": provider,
                "model": model,
                "config_provider": config_provider,
                "fallback_used": fallback_used,
                "raw": text,
                "error": "LLM response was not valid JSON",
            }
        return {
            "status": "success",
            "provider": provider,
            "model": model,
            "config_provider": config_provider,
            "fallback_used": fallback_used,
            "raw": text,
            "parsed": parsed,
        }
    except Exception as e:
        return {
            "status": "error",
            "provider": provider,
            "model": model,
            "config_provider": config_provider,
            "fallback_used": fallback_used,
            "error": str(e),
        }


__all__ = [
    "_chat_completion_json",
    "_extract_first_json_object",
    "_json_text",
    "_ollama_model",
    "_utm_nfz_conflict_feedback",
]
