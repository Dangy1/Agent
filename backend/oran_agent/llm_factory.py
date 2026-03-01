"""Shared LLM model factory for all agent graphs and copilot helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

from .config.runtime_llm import get_llm_runtime_config


@dataclass(frozen=True)
class LLMModelMeta:
    provider: str
    model: str
    config_provider: str
    fallback_used: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "config_provider": self.config_provider,
            "fallback_used": self.fallback_used,
        }


def _provider_order(config_provider: str, cfg: Dict[str, Any]) -> Iterable[str]:
    provider = str(config_provider or "ollama").strip().lower()
    if provider == "auto":
        return ("ollama", "openai")
    if provider == "ollama":
        if bool(cfg.get("fallback_to_openai", True)):
            return ("ollama", "openai")
        return ("ollama",)
    if provider == "openai":
        return ("openai", "ollama")
    return ("ollama", "openai")


def _build_ollama_model(cfg: Dict[str, Any], temperature: float) -> Tuple[Any, str]:
    try:
        from langchain_ollama import ChatOllama  # type: ignore
    except Exception as e:  # pragma: no cover - optional runtime dependency
        raise RuntimeError("langchain_ollama is not installed") from e

    model_name = str(cfg.get("ollama_model", "") or "").strip() or "gpt-oss:latest"
    base_url = str(cfg.get("ollama_url", "") or "").strip()
    if not base_url:
        raise RuntimeError("OLLAMA_URL is empty")
    model = ChatOllama(model=model_name, base_url=base_url, temperature=temperature)
    return model, model_name


def _build_openai_model(cfg: Dict[str, Any], temperature: float) -> Tuple[Any, str]:
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
    except Exception as e:  # pragma: no cover - optional runtime dependency
        raise RuntimeError("langchain_openai is not installed") from e

    api_key = str(cfg.get("openai_api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    model_name = str(cfg.get("openai_model", "") or "").strip() or "gpt-4o-mini"
    base_url = str(cfg.get("openai_base_url", "") or "").strip() or None
    model = ChatOpenAI(model=model_name, api_key=api_key, base_url=base_url, temperature=temperature)
    return model, model_name


def build_chat_model_from_config(
    cfg: Dict[str, Any],
    *,
    temperature: float = 0.0,
) -> Tuple[Any, LLMModelMeta]:
    config_provider = str(cfg.get("provider", "ollama") or "ollama").strip().lower()
    errors: List[str] = []
    order = list(_provider_order(config_provider, cfg))

    for idx, provider in enumerate(order):
        try:
            if provider == "ollama":
                model, model_name = _build_ollama_model(cfg, temperature)
            elif provider == "openai":
                model, model_name = _build_openai_model(cfg, temperature)
            else:
                errors.append(f"{provider}: unsupported provider")
                continue
            meta = LLMModelMeta(
                provider=provider,
                model=model_name,
                config_provider=config_provider,
                fallback_used=(idx > 0 and provider != config_provider),
            )
            return model, meta
        except Exception as e:
            errors.append(f"{provider}: {e}")

    raise RuntimeError(
        "Unable to initialize any LLM provider. "
        f"Configured provider='{config_provider}'. "
        f"Errors: {'; '.join(errors) if errors else 'none'}"
    )


def build_chat_model(*, temperature: float = 0.0) -> Tuple[Any, LLMModelMeta]:
    cfg = get_llm_runtime_config()
    return build_chat_model_from_config(cfg, temperature=temperature)

