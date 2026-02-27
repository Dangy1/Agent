"""Shared UAV copilot LLM/JSON helpers."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

try:  # pragma: no cover - optional dependency at runtime
    from langchain_ollama import ChatOllama
except Exception:  # pragma: no cover
    ChatOllama = None  # type: ignore[assignment]

from oran_agent.config.settings import MODEL, OLLAMA_URL


_UAV_COPILOT_OLLAMA_MODEL: Any | None = None


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


def _ollama_model() -> Any | None:
    global _UAV_COPILOT_OLLAMA_MODEL
    if _UAV_COPILOT_OLLAMA_MODEL is not None:
        return _UAV_COPILOT_OLLAMA_MODEL
    if ChatOllama is None:
        return None
    model_name = str(os.getenv("UAV_COPILOT_OLLAMA_MODEL", MODEL) or MODEL).strip()
    base_url = str(os.getenv("UAV_COPILOT_OLLAMA_URL", OLLAMA_URL) or OLLAMA_URL).strip()
    try:
        _UAV_COPILOT_OLLAMA_MODEL = ChatOllama(model=model_name, base_url=base_url, temperature=0)
        return _UAV_COPILOT_OLLAMA_MODEL
    except Exception:
        return None


def _chat_completion_json(
    *,
    system_prompt: str,
    user_payload: Dict[str, Any],
    unavailable_error_message: str = "Ollama model not available (langchain_ollama missing or Ollama not reachable)",
) -> Dict[str, Any]:
    model_obj = _ollama_model()
    model = str(os.getenv("UAV_COPILOT_OLLAMA_MODEL", MODEL) or MODEL).strip()
    if model_obj is None:
        return {"status": "unavailable", "error": unavailable_error_message}
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
            return {"status": "error", "model": model, "raw": text, "error": "LLM response was not valid JSON"}
        return {"status": "success", "model": model, "raw": text, "parsed": parsed}
    except Exception as e:
        return {"status": "error", "model": model, "error": str(e)}


__all__ = [
    "_chat_completion_json",
    "_extract_first_json_object",
    "_json_text",
    "_ollama_model",
    "_utm_nfz_conflict_feedback",
]
