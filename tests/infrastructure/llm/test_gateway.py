import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from energytool.application.trace import collecting_llm_calls
from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.gateway import (
    LlmGateway,
    LlmRequestError,
    LlmResponseError,
    LlmUnavailableError,
)

Handler = Callable[[httpx.Request], httpx.Response]


def make_gateway(handler: Handler, **env: str) -> tuple[LlmGateway, list[float]]:
    settings = Settings.from_env(
        {"LLM_BASE_URL": "http://proxy/v1", "LLM_API_KEY": "key", "LLM_MODEL": "qwen", **env}
    )
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LlmGateway(settings, client=client, sleep=sleeps.append), sleeps


def chat_body(content: str, finish: str = "stop") -> dict[str, Any]:
    return {
        "model": "qwen",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
    }


def test_chat_sends_openai_payload_and_records_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_body("<think>хм</think> Ответ "))

    gateway, _ = make_gateway(handler)
    with collecting_llm_calls() as calls:
        result = gateway.chat([{"role": "user", "content": "привет"}], temperature=0.0)

    assert result.text == "Ответ"
    assert (result.tokens_in, result.tokens_out) == (11, 5)
    request = seen[0]
    assert str(request.url) == "http://proxy/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer key"
    payload = json.loads(request.content)
    assert payload["model"] == "qwen"
    assert payload["temperature"] == 0.0
    assert payload["messages"] == [{"role": "user", "content": "привет"}]
    assert [(c.kind, c.ok, c.tokens_in) for c in calls] == [("chat", True, 11)]


def test_retries_on_429_and_5xx_then_succeeds() -> None:
    replies = iter(
        [
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(503),
            httpx.Response(200, json=chat_body("ok")),
        ]
    )
    gateway, sleeps = make_gateway(lambda _: next(replies))
    with collecting_llm_calls() as calls:
        result = gateway.chat([{"role": "user", "content": "x"}])
    assert result.text == "ok"
    assert result.retries == 2
    assert sleeps == [2.0, 1.0]
    assert calls[0].retries == 2


def test_gives_up_after_max_retries_on_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    gateway, sleeps = make_gateway(handler, LLM_MAX_RETRIES="2")
    with collecting_llm_calls() as calls, pytest.raises(LlmUnavailableError, match="попыток: 3"):
        gateway.chat([{"role": "user", "content": "x"}])
    assert sleeps == [0.5, 1.0]
    assert calls[0].ok is False


def test_client_errors_are_not_retried() -> None:
    gateway, sleeps = make_gateway(lambda _: httpx.Response(401, text="token_not_found_in_db"))
    with pytest.raises(LlmRequestError, match="401"):
        gateway.chat([{"role": "user", "content": "x"}])
    assert sleeps == []


def test_missing_endpoint_fails_fast() -> None:
    settings = Settings.from_env({})

    def unreachable(_: httpx.Request) -> httpx.Response:
        raise AssertionError("запрос не должен уйти")

    gateway = LlmGateway(settings, client=httpx.Client(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(LlmRequestError, match="не заданы"):
        gateway.chat([{"role": "user", "content": "x"}])


def test_malformed_chat_body_raises_response_error() -> None:
    gateway, _ = make_gateway(lambda _: httpx.Response(200, json={"error": "x"}))
    with pytest.raises(LlmResponseError):
        gateway.chat([{"role": "user", "content": "x"}])


def test_embed_restores_order_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]},
        )

    gateway, _ = make_gateway(handler)
    assert gateway.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_embed_count_mismatch_raises() -> None:
    gateway, _ = make_gateway(lambda _: httpx.Response(200, json={"data": []}))
    with pytest.raises(LlmResponseError, match="ожидалось 1"):
        gateway.embed(["a"])


def test_rerank_sorts_by_score_and_drops_bad_indices() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["top_n"] == 2
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 7, "relevance_score": 1.0},
                ]
            },
        )

    gateway, _ = make_gateway(handler)
    assert gateway.rerank("q", ["d0", "d1"], top_n=5) == [(1, 0.9), (0, 0.1)]


def test_separate_reranker_endpoint_is_used() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url} {request.headers['authorization']}")
        return httpx.Response(200, json={"results": []})

    gateway, _ = make_gateway(handler, RERANKER_BASE_URL="http://rr/api", RERANKER_API_KEY="rk")
    gateway.rerank("q", ["d"], top_n=1)
    assert seen == ["http://rr/api/rerank Bearer rk"]


def test_openrouter_profile_disables_reasoning() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=chat_body("ok"))

    gateway, _ = make_gateway(handler, MODELS_PROFILE="openrouter")
    gateway.chat([{"role": "user", "content": "x"}])
    assert seen[0]["reasoning"] == {"enabled": False}
    assert "chat_template_kwargs" not in seen[0]


def test_platform_drops_thinking_params_after_400_once() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if "chat_template_kwargs" in payload:
            return httpx.Response(400, text="unknown field chat_template_kwargs")
        return httpx.Response(200, json=chat_body("ok"))

    gateway, sleeps = make_gateway(handler)
    assert gateway.chat([{"role": "user", "content": "1"}]).text == "ok"
    assert gateway.chat([{"role": "user", "content": "2"}]).text == "ok"
    assert ["chat_template_kwargs" in p for p in seen] == [True, False, False]
    assert sleeps == []


def test_other_client_errors_are_not_masked_by_thinking_fallback() -> None:
    gateway, _ = make_gateway(lambda _: httpx.Response(401))
    with pytest.raises(LlmRequestError) as error:
        gateway.chat([{"role": "user", "content": "x"}])
    assert error.value.status_code == 401


def test_thinking_params_can_be_turned_off() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=chat_body("ok"))

    gateway, _ = make_gateway(handler, LLM_DISABLE_THINKING="false")
    gateway.chat([{"role": "user", "content": "x"}])
    assert "chat_template_kwargs" not in seen[0]
    assert "reasoning" not in seen[0]
