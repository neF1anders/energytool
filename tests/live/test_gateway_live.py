"""Живые проверки моделей: `uv run --env-file .env pytest -m live`."""

import os

import pytest

from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.gateway import LlmGateway

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def gateway() -> LlmGateway:
    settings = Settings.from_env(os.environ)
    if not settings.llm.configured:
        pytest.skip("нет LLM_API_KEY / LLM_MODEL в окружении")
    return LlmGateway(settings)


def test_chat_answers_without_thinking(gateway: LlmGateway) -> None:
    result = gateway.chat([{"role": "user", "content": "Ответь одним словом: работает?"}])
    assert result.text
    assert result.tokens_out < 20, "thinking не выключен"


def test_embeddings_are_bge_m3_sized(gateway: LlmGateway) -> None:
    (vector,) = gateway.embed(["счётчик электроэнергии"])
    assert len(vector) == 1024


def test_reranker_prefers_relevant_document(gateway: LlmGateway) -> None:
    ranked = gateway.rerank("учёт электроэнергии", ["кровля и панели", "счётчики и АСКУЭ"], 2)
    assert ranked[0][0] == 1
