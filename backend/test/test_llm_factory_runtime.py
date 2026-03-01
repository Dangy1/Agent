from __future__ import annotations

import sys
import types

import pytest

from oran_agent.llm_factory import build_chat_model_from_config


def _install_dummy_module(monkeypatch: pytest.MonkeyPatch, name: str, class_name: str) -> None:
    mod = types.ModuleType(name)

    class DummyModel:
        def __init__(self, **kwargs):
            self.kwargs = dict(kwargs)

    setattr(mod, class_name, DummyModel)
    monkeypatch.setitem(sys.modules, name, mod)


def test_build_chat_model_from_config_ollama_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_dummy_module(monkeypatch, "langchain_ollama", "ChatOllama")
    cfg = {
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "gpt-oss:latest",
        "fallback_to_openai": True,
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "openai_base_url": "",
    }
    model, meta = build_chat_model_from_config(cfg, temperature=0.2)
    assert model.kwargs["model"] == "gpt-oss:latest"
    assert model.kwargs["base_url"] == "http://127.0.0.1:11434"
    assert model.kwargs["temperature"] == 0.2
    assert meta.provider == "ollama"
    assert meta.fallback_used is False


def test_build_chat_model_from_config_ollama_fallback_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure Ollama import fails.
    monkeypatch.delitem(sys.modules, "langchain_ollama", raising=False)
    _install_dummy_module(monkeypatch, "langchain_openai", "ChatOpenAI")

    cfg = {
        "provider": "ollama",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "gpt-oss:latest",
        "fallback_to_openai": True,
        "openai_api_key": "test-key",
        "openai_model": "gpt-4o-mini",
        "openai_base_url": "",
    }
    model, meta = build_chat_model_from_config(cfg, temperature=0.0)
    assert model.kwargs["model"] == "gpt-4o-mini"
    assert model.kwargs["api_key"] == "test-key"
    assert meta.provider == "openai"
    assert meta.config_provider == "ollama"
    assert meta.fallback_used is True


def test_build_chat_model_from_config_openai_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_dummy_module(monkeypatch, "langchain_openai", "ChatOpenAI")
    cfg = {
        "provider": "openai",
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        "openai_base_url": "",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "gpt-oss:latest",
        "fallback_to_openai": True,
    }
    with pytest.raises(RuntimeError, match="Unable to initialize any LLM provider"):
        build_chat_model_from_config(cfg, temperature=0.0)

