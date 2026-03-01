"""Runtime LLM provider configuration for all agents.

This mirrors the MCP runtime config pattern: environment defaults with in-memory overrides.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .settings import MODEL, OLLAMA_URL

_OVERRIDES: Dict[str, Any] = {}
_VALID_PROVIDERS = {"ollama", "openai", "auto"}


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_provider(value: Any, *, default: str = "ollama") -> str:
    provider = str(value or "").strip().lower()
    return provider if provider in _VALID_PROVIDERS else default


def _normalize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Any] = {}

    if "provider" in payload and payload.get("provider") is not None:
        out["provider"] = _normalize_provider(payload.get("provider"))
    if "ollama_url" in payload and payload.get("ollama_url") is not None:
        out["ollama_url"] = str(payload.get("ollama_url")).strip()
    if "ollama_model" in payload and payload.get("ollama_model") is not None:
        out["ollama_model"] = str(payload.get("ollama_model")).strip()
    if "openai_model" in payload and payload.get("openai_model") is not None:
        out["openai_model"] = str(payload.get("openai_model")).strip()
    if "openai_base_url" in payload and payload.get("openai_base_url") is not None:
        out["openai_base_url"] = str(payload.get("openai_base_url")).strip()
    if "openai_api_key" in payload and payload.get("openai_api_key") is not None:
        out["openai_api_key"] = str(payload.get("openai_api_key")).strip()
    if "fallback_to_openai" in payload and payload.get("fallback_to_openai") is not None:
        out["fallback_to_openai"] = _to_bool(payload.get("fallback_to_openai"), default=True)

    return out


def get_llm_runtime_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "provider": _normalize_provider(os.getenv("LLM_PROVIDER", "ollama"), default="ollama"),
        "ollama_url": str(os.getenv("OLLAMA_URL", OLLAMA_URL) or OLLAMA_URL).strip(),
        "ollama_model": str(os.getenv("OLLAMA_MODEL", MODEL) or MODEL).strip(),
        "openai_model": str(os.getenv("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip(),
        "openai_base_url": str(os.getenv("OPENAI_BASE_URL", "") or "").strip(),
        "openai_api_key": str(os.getenv("OPENAI_API_KEY", "") or "").strip(),
        "fallback_to_openai": _to_bool(os.getenv("LLM_FALLBACK_TO_OPENAI", "1"), default=True),
    }
    cfg.update(_OVERRIDES)
    cfg["provider"] = _normalize_provider(cfg.get("provider"), default="ollama")
    cfg["fallback_to_openai"] = _to_bool(cfg.get("fallback_to_openai"), default=True)
    return cfg


def set_llm_runtime_overrides(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _OVERRIDES.clear()
    _OVERRIDES.update(_normalize_payload(payload))
    return get_llm_runtime_config()


def patch_llm_runtime_overrides(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _OVERRIDES.update(_normalize_payload(payload))
    return get_llm_runtime_config()


def clear_llm_runtime_overrides() -> Dict[str, Any]:
    _OVERRIDES.clear()
    return get_llm_runtime_config()


def list_llm_providers() -> Dict[str, Dict[str, Any]]:
    return {
        "ollama": {
            "label": "Ollama",
            "requires_api_key": False,
            "default_model_env": "OLLAMA_MODEL",
            "default_url_env": "OLLAMA_URL",
        },
        "openai": {
            "label": "OpenAI",
            "requires_api_key": True,
            "default_model_env": "OPENAI_MODEL",
            "default_key_env": "OPENAI_API_KEY",
            "default_base_url_env": "OPENAI_BASE_URL",
        },
        "auto": {
            "label": "Auto (Ollama -> OpenAI fallback)",
            "requires_api_key": False,
        },
    }


def runtime_snapshot_for_ui(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or get_llm_runtime_config()
    provider = _normalize_provider(cfg.get("provider"), default="ollama")
    return {
        "provider": provider,
        "ollama": {
            "url": str(cfg.get("ollama_url", "") or ""),
            "model": str(cfg.get("ollama_model", "") or ""),
        },
        "openai": {
            "model": str(cfg.get("openai_model", "") or ""),
            "base_url": str(cfg.get("openai_base_url", "") or ""),
            "api_key_set": bool(str(cfg.get("openai_api_key", "") or "").strip()),
        },
        "fallback_to_openai": bool(cfg.get("fallback_to_openai", True)),
    }

