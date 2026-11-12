"""LLM Gateway: httpx-клиент к OpenAI-совместимому прокси (docs/spec.md §8.1).

Три операции: chat/completions, embeddings, rerank. У каждой свой эндпоинт из Settings
(на платформе все три — один прокси). Общее для всех:
- один httpx.Client (потокобезопасен) и общий семафор по MAX_WORKERS;
- повторы на 429/5xx/таймаут/сетевую ошибку: до max_retries, пауза 0.5·2^n с, Retry-After
  уважается (не дольше 8 с); прочие 4xx — сразу LlmRequestError;
- каждый вызов → LlmCall в лог и в сборщик хода (application.trace.record_llm_call).

Gateway ничего не знает про домен: адаптеры портов (extractor, responder, embedder,
reranker) строятся поверх него.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

import httpx

from energytool.application.ports import ExternalServiceError
from energytool.application.trace import LlmCall, LlmCallKind, record_llm_call
from energytool.infrastructure.config import ModelEndpoint, ModelsProfile, Settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MAX_RETRY_AFTER_SEC = 8.0
_BASE_BACKOFF_SEC = 0.5
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LlmGatewayError(ExternalServiceError):
    """Базовая ошибка обращения к моделям."""


class LlmUnavailableError(LlmGatewayError):
    """Модель не ответила после всех повторов (сеть, таймаут, 429, 5xx)."""


class LlmRequestError(LlmGatewayError):
    """Запрос отклонён (4xx кроме ретраемых): ошибка конфигурации или запроса."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LlmResponseError(LlmGatewayError):
    """Ответ пришёл, но не соответствует протоколу."""


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    model: str
    finish_reason: str
    tokens_in: int
    tokens_out: int
    ms: float
    retries: int


@dataclass(frozen=True, slots=True)
class _Response:
    body: dict[str, Any]
    ms: float
    retries: int


@dataclass(frozen=True, slots=True)
class _Outcome:
    """Итог одной попытки: body при успехе; иначе error и retry_delay (None — не повторять)."""

    body: dict[str, Any] | None = None
    error: str = ""
    retry_delay: float | None = None
    status_code: int | None = None


class LlmGateway:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client()
        self._sleep = sleep
        self._slots = threading.BoundedSemaphore(settings.max_workers)
        self._thinking_off = _thinking_off_params(settings)

    def close(self) -> None:
        self._client.close()

    # --- операции ----------------------------------------------------------------------------

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> ChatResult:
        endpoint = self._settings.llm
        payload: dict[str, Any] = {
            "model": endpoint.model,
            "messages": list(messages),
            "temperature": self._settings.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self._settings.max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if extra_body:
            payload.update(extra_body)
        response = self._post_chat(payload)
        result = _parse_chat(response, endpoint.model)
        if not result.text:
            logger.warning(
                "chat returned empty content",
                extra={"data": {"finish_reason": result.finish_reason, "model": result.model}},
            )
        return result

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        endpoint = self._settings.embedder
        payload = {"model": endpoint.model, "input": list(texts)}
        response = self._post(
            "embed", endpoint, "/embeddings", payload, self._settings.timeouts.embed_sec
        )
        return _parse_embeddings(response.body, len(texts))

    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        """(индекс документа, скор) по убыванию скора."""
        endpoint = self._settings.reranker
        payload = {
            "model": endpoint.model,
            "query": query,
            "documents": list(documents),
            "top_n": min(top_n, len(documents)),
        }
        response = self._post(
            "rerank", endpoint, "/rerank", payload, self._settings.timeouts.rerank_sec
        )
        return _parse_rerank(response.body, len(documents))

    # --- транспорт ---------------------------------------------------------------------------

    def _post_chat(self, payload: dict[str, Any]) -> _Response:
        """chat/completions с выключенным thinking. Если прокси не знает параметр (400),
        один раз пишем warning и дальше шлём без него: ответ важнее скорости."""
        endpoint = self._settings.llm
        timeout = self._settings.timeouts.chat_sec
        thinking_off = self._thinking_off
        if not thinking_off:
            return self._post("chat", endpoint, "/chat/completions", payload, timeout)
        try:
            return self._post(
                "chat", endpoint, "/chat/completions", {**thinking_off, **payload}, timeout
            )
        except LlmRequestError as exc:
            if exc.status_code != 400:
                raise
            self._thinking_off = {}
            logger.warning(
                "thinking-off params rejected by proxy, continuing without them",
                extra={"data": {"params": thinking_off, "error": str(exc)[:300]}},
            )
            return self._post("chat", endpoint, "/chat/completions", payload, timeout)

    def _post(
        self,
        kind: LlmCallKind,
        endpoint: ModelEndpoint,
        path: str,
        payload: dict[str, Any],
        timeout_sec: float,
    ) -> _Response:
        if not endpoint.base_url or not endpoint.api_key:
            raise LlmRequestError(f"{kind}: не заданы адрес или ключ эндпоинта")
        url = endpoint.base_url + path
        headers = {"Authorization": f"Bearer {endpoint.api_key}"}
        started = time.perf_counter()
        attempt = 0
        while True:
            outcome = self._attempt(url, headers, payload, timeout_sec, attempt)
            if outcome.body is not None:
                response = _Response(outcome.body, _ms_since(started), attempt)
                self._report(kind, endpoint.model, response)
                return response
            retryable = outcome.retry_delay is not None
            if not retryable or attempt >= self._settings.max_retries:
                self._report_failure(kind, endpoint.model, started, attempt, outcome.error)
                if not retryable:
                    raise LlmRequestError(f"{kind}: {outcome.error}", outcome.status_code)
                raise LlmUnavailableError(f"{kind}: {outcome.error}; попыток: {attempt + 1}")
            attempt += 1
            delay = outcome.retry_delay or 0.0
            logger.warning(
                "%s retry %d/%d in %.1fs: %s",
                kind,
                attempt,
                self._settings.max_retries,
                delay,
                outcome.error,
            )
            self._sleep(delay)

    def _attempt(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_sec: float,
        attempt: int,
    ) -> _Outcome:
        backoff = _BASE_BACKOFF_SEC * 2**attempt
        with self._slots:
            try:
                reply = self._client.post(url, headers=headers, json=payload, timeout=timeout_sec)
            except httpx.TimeoutException as exc:
                return _Outcome(error=f"timeout ({type(exc).__name__})", retry_delay=backoff)
            except httpx.TransportError as exc:
                return _Outcome(error=f"transport ({type(exc).__name__})", retry_delay=backoff)
        if reply.status_code >= 400:
            error = f"HTTP {reply.status_code}: {reply.text[:300]}"
            if reply.status_code in _RETRYABLE_STATUS:
                return _Outcome(error=error, retry_delay=_retry_after(reply) or backoff)
            return _Outcome(error=error, status_code=reply.status_code)
        try:
            body = reply.json()
        except ValueError:
            return _Outcome(error=f"не JSON: {reply.text[:300]}")
        if not isinstance(body, dict):
            return _Outcome(error="ответ не объект JSON")
        return _Outcome(body=body)

    def _report(self, kind: LlmCallKind, model: str, response: _Response) -> None:
        usage = response.body.get("usage") or {}
        call = LlmCall(
            kind=kind,
            model=str(response.body.get("model") or model),
            ms=response.ms,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            retries=response.retries,
        )
        record_llm_call(call)
        logger.debug("llm call", extra={"data": call})

    def _report_failure(
        self, kind: LlmCallKind, model: str, started: float, attempt: int, error: str
    ) -> None:
        call = LlmCall(
            kind=kind,
            model=model,
            ms=_ms_since(started),
            retries=attempt,
            ok=False,
            error=error[:300],
        )
        record_llm_call(call)
        logger.error("llm call failed", extra={"data": call})


def _thinking_off_params(settings: Settings) -> dict[str, Any]:
    """Как выключить thinking Qwen. OpenRouter: reasoning.enabled=false (проверено вживую,
    2026-11-12: 2 выходных токена вместо ~70–90, chat_template_kwargs он игнорирует).
    Прокси платформы — предположительно vLLM, у него chat_template_kwargs."""
    if not settings.disable_thinking:
        return {}
    if settings.profile is ModelsProfile.OPENROUTER:
        return {"reasoning": {"enabled": False}}
    return {"chat_template_kwargs": {"enable_thinking": False}}


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _retry_after(reply: httpx.Response) -> float | None:
    raw = reply.headers.get("retry-after", "")
    try:
        return min(_MAX_RETRY_AFTER_SEC, max(0.0, float(raw)))
    except ValueError:
        return None


def _parse_chat(response: _Response, requested_model: str) -> ChatResult:
    body = response.body
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmResponseError(f"chat: нет choices[0].message: {str(body)[:300]}") from exc
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise LlmResponseError("chat: content не строка")
    usage = body.get("usage") or {}
    return ChatResult(
        text=_THINK_BLOCK.sub("", content).strip(),
        model=str(body.get("model") or requested_model),
        finish_reason=str(choice.get("finish_reason") or ""),
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        ms=response.ms,
        retries=response.retries,
    )


def _parse_embeddings(body: dict[str, Any], expected: int) -> list[list[float]]:
    items = body.get("data")
    if not isinstance(items, list) or len(items) != expected:
        got = len(items) if isinstance(items, list) else "нет data"
        raise LlmResponseError(f"embed: ожидалось {expected} векторов, получено {got}")
    vectors: list[list[float] | None] = [None] * expected
    for position, item in enumerate(items):
        index = int(item.get("index", position))
        if not 0 <= index < expected:
            raise LlmResponseError(f"embed: index {index} вне диапазона")
        vectors[index] = [float(x) for x in item["embedding"]]
    if any(v is None for v in vectors):
        raise LlmResponseError("embed: получены не все векторы")
    return [v for v in vectors if v is not None]


def _parse_rerank(body: dict[str, Any], documents: int) -> list[tuple[int, float]]:
    items = body.get("results", body.get("data"))
    if not isinstance(items, list):
        raise LlmResponseError(f"rerank: нет results: {str(body)[:300]}")
    ranked: list[tuple[int, float]] = []
    for item in items:
        index = int(item["index"])
        score = float(item.get("relevance_score", item.get("score", 0.0)))
        if 0 <= index < documents:
            ranked.append((index, score))
    return sorted(ranked, key=lambda pair: pair[1], reverse=True)
