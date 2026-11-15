import json

import httpx

from energytool.domain.state import Message, Role
from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.condenser import LlmQuestionCondenser
from energytool.infrastructure.llm.gateway import LlmGateway

HISTORY = [
    Message(Role.USER, "У нас бизнес-центр", 0),
    Message(Role.ASSISTANT, "Какая задача?", 1),
    Message(Role.USER, "С чего начать?", 2),
]


def condenser(handler: httpx.MockTransport) -> LlmQuestionCondenser:
    env = {"LLM_BASE_URL": "http://proxy", "LLM_API_KEY": "k", "LLM_MAX_RETRIES": "0"}
    gateway = LlmGateway(Settings.from_env(env), httpx.Client(transport=handler))
    return LlmQuestionCondenser(gateway)


def test_condense_returns_single_line_question_built_from_dialog() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][-1]["content"])
        content = "«С чего начать учёт\nв бизнес-центре?»"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    assert condenser(httpx.MockTransport(handle)).condense(HISTORY) == (
        "С чего начать учёт в бизнес-центре?"
    )
    assert "Клиент: У нас бизнес-центр\nКонсультант: Какая задача?" in seen[0]


def test_condense_failure_returns_empty_string() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, text="bad"))
    assert condenser(transport).condense(HISTORY) == ""


def test_llm_cache_dir_answers_repeated_request_from_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    env = {"LLM_BASE_URL": "http://proxy", "LLM_API_KEY": "k", "LLM_CACHE_DIR": str(tmp_path)}
    gateway = LlmGateway(
        Settings.from_env(env), httpx.Client(transport=httpx.MockTransport(handle))
    )
    assert gateway.rerank("вопрос", ["документ"], 1) == gateway.rerank("вопрос", ["документ"], 1)
    assert len(calls) == 1
