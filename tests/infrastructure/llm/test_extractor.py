import json

import httpx
import pytest

from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Intent
from energytool.domain.products import Product
from energytool.domain.state import ConversationState, Message, Role
from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.extractor import (
    ExtractionParseError,
    LlmFactExtractor,
    build_messages,
    parse_extraction,
)
from energytool.infrastructure.llm.gateway import LlmGateway

CATALOG = ContractRegistry().slots
GOOD = {
    "intent": "mixed",
    "slot_updates": [
        {"slot_id": "max_power_kw", "value": 1.1, "quote": "1,1 МВт", "unit": "МВт"},
        {"slot_id": "nope", "value": 1, "quote": "x"},
        {"slot_id": "segment", "value": {"bad": 1}, "quote": "x"},
    ],
    "declined": ["region", "unknown_slot"],
    "mentions": ["orem", "teleport"],
    "wants_contact": False,
    "search_query": "выход на оптовый рынок 1,1 МВт",
    "question": "Подходим ли мы?",
}


def test_parse_keeps_valid_items_only() -> None:
    text = "Вот ответ:\n```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    extraction = parse_extraction(text, CATALOG)
    assert extraction.intent is Intent.MIXED
    assert [(u.slot_id, u.value, u.unit) for u in extraction.slot_updates] == [
        ("max_power_kw", 1.1, "МВт")
    ]
    assert extraction.declined == ("region",)
    assert extraction.mentions == (Product.OREM,)
    assert extraction.search_query.startswith("выход")


def test_parse_unknown_intent_defaults_to_question() -> None:
    assert parse_extraction('{"intent": "hello"}', CATALOG).intent is Intent.QUESTION


@pytest.mark.parametrize("text", ["нет json", "{битый", "[1, 2]"])
def test_parse_errors(text: str) -> None:
    with pytest.raises(ExtractionParseError):
        parse_extraction(text, CATALOG)


def test_prompt_has_catalog_choices_known_and_dialog() -> None:
    state = ConversationState("s")
    history = [Message(Role.USER, "Мы склад", 1)]
    system, user = build_messages(history, state, CATALOG)
    assert "segment | Тип организации | choice | industry=" in system["content"]
    assert "max_power_kw | " in system["content"] and "кВт" in system["content"]
    assert "Клиент: Мы склад" in user["content"]
    assert "Уже известно о клиенте: ничего" in user["content"]


def test_extractor_retries_once_on_bad_json() -> None:
    replies = iter(["не json", json.dumps({"intent": "info"})])

    def handler(request: httpx.Request) -> httpx.Response:
        content = next(replies)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    settings = Settings.from_env({"LLM_BASE_URL": "http://p", "LLM_API_KEY": "k"})
    gateway = LlmGateway(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    extraction = LlmFactExtractor(gateway).extract(
        [Message(Role.USER, "x", 1)], ConversationState("s"), CATALOG
    )
    assert extraction.intent is Intent.INFO
