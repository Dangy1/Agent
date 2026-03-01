from __future__ import annotations

from oran_agent.config.runtime_llm import (
    clear_llm_runtime_overrides,
    get_llm_runtime_config,
    patch_llm_runtime_overrides,
    runtime_snapshot_for_ui,
    set_llm_runtime_overrides,
)


def test_runtime_snapshot_masks_api_key() -> None:
    try:
        cfg = set_llm_runtime_overrides(
            {
                "provider": "openai",
                "openai_api_key": "sk-test",
                "openai_model": "gpt-4o-mini",
            }
        )
        snap = runtime_snapshot_for_ui(cfg)
        assert snap["provider"] == "openai"
        assert snap["openai"]["api_key_set"] is True
        assert "openai_api_key" not in snap
    finally:
        clear_llm_runtime_overrides()


def test_patch_llm_runtime_overrides_preserves_existing() -> None:
    try:
        set_llm_runtime_overrides({"provider": "ollama", "ollama_model": "gpt-oss:latest"})
        patched = patch_llm_runtime_overrides({"openai_model": "gpt-4o-mini"})
        assert patched["provider"] == "ollama"
        assert patched["ollama_model"] == "gpt-oss:latest"
        assert patched["openai_model"] == "gpt-4o-mini"
    finally:
        clear_llm_runtime_overrides()


def test_get_llm_runtime_config_normalizes_provider() -> None:
    try:
        set_llm_runtime_overrides({"provider": "invalid-provider"})
        cfg = get_llm_runtime_config()
        assert cfg["provider"] == "ollama"
    finally:
        clear_llm_runtime_overrides()

