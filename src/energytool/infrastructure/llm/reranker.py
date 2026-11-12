"""Порт Reranker поверх gateway. Документы обрезаются: длинные справочные разделы
(до ~5 тыс. символов) иначе раздувают время и стоимость вызова без пользы для ранга."""

from __future__ import annotations

from collections.abc import Sequence

from energytool.infrastructure.llm.gateway import LlmGateway

MAX_DOCUMENT_CHARS = 2000


class GatewayReranker:
    def __init__(self, gateway: LlmGateway, max_document_chars: int = MAX_DOCUMENT_CHARS) -> None:
        self._gateway = gateway
        self._max_chars = max_document_chars

    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        trimmed = [doc[: self._max_chars] for doc in documents]
        return self._gateway.rerank(query, trimmed, top_n)
