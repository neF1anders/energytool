from pathlib import Path

import pytest

from energytool.infrastructure.config import (
    OPENROUTER_BASE_URL,
    PLATFORM_LLM_MODEL,
    ConfigError,
    ModelsProfile,
    Settings,
)


def test_platform_profile_reads_synonyms_and_shares_key() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_BASE_URL": "http://proxy:8000/v1/",
            "OPENAI_API_KEY": "secret",
            "GEN_MODEL": "qwen-x",
            "DATA_DIR": "/data",
        }
    )
    assert settings.profile is ModelsProfile.PLATFORM
    assert settings.llm.base_url == "http://proxy:8000/v1"
    assert settings.llm.model == "qwen-x"
    assert settings.embedder.base_url == settings.llm.base_url
    assert settings.embedder.api_key == "secret"
    assert settings.reranker.api_key == "secret"
    assert settings.data_dir == Path("/data")


def test_platform_model_defaults_when_env_has_placeholder() -> None:
    settings = Settings.from_env({"LLM_MODEL": "LLM_MODEL"})
    assert settings.llm.model == PLATFORM_LLM_MODEL


def test_openrouter_profile_defaults_base_url_and_requires_model() -> None:
    settings = Settings.from_env({"MODELS_PROFILE": "openrouter", "LLM_API_KEY": "k"})
    assert settings.llm.base_url == OPENROUTER_BASE_URL
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        settings.require_llm()


def test_numbers_are_clamped_and_bad_values_fall_back() -> None:
    settings = Settings.from_env({"MAX_WORKERS": "100", "TEMPERATURE": "abc"})
    assert settings.max_workers == 16
    assert settings.temperature == 0.1


def test_separate_embedder_key_overrides_llm_key() -> None:
    settings = Settings.from_env({"LLM_API_KEY": "a", "EMBEDDER_API_KEY": "b"})
    assert settings.embedder.api_key == "b"
    assert settings.reranker.api_key == "a"


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ConfigError, match="MODELS_PROFILE"):
        Settings.from_env({"MODELS_PROFILE": "cloud"})


def test_keys_never_leak_into_summary_or_repr() -> None:
    settings = Settings.from_env({"LLM_API_KEY": "top-secret", "LLM_BASE_URL": "http://x"})
    assert "top-secret" not in repr(settings)
    assert "top-secret" not in str(settings.safe_summary())
    assert settings.safe_summary()["llm"] == {
        "base_url": "http://x",
        "model": PLATFORM_LLM_MODEL,
        "has_key": True,
    }


@pytest.mark.parametrize(
    ("raw", "expected"), [("full", "full"), ("dialogs", "dialogs"), ("", "naive"), ("x", "naive")]
)
def test_batch_pipeline_accepts_known_values_only(raw: str, expected: str) -> None:
    env = {"LLM_BASE_URL": "http://p/v1", "LLM_API_KEY": "k", "BATCH_PIPELINE": raw}
    assert Settings.from_env(env).batch_pipeline == expected


def test_full_batch_tuning_flags_default_to_variant_i_and_clamp() -> None:
    default = Settings.from_env({})
    assert (default.batch_rerank_question, default.batch_leader_probability) == (False, 0.45)
    tuned = Settings.from_env({"BATCH_RERANK_QUESTION": "1", "BATCH_LEADER_PROBABILITY": "0.05"})
    assert (tuned.batch_rerank_question, tuned.batch_leader_probability) == (True, 0.2)
