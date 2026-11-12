"""Use case RetrieveKnowledge: поиск по базе знаний.

    RetrieveKnowledge(query: KnowledgeQuery) -> KnowledgeResult

Вход — кортеж сниппетов (domain/knowledge.py), выход — Evidence в терминах домена.
Use case не знает про диалог: запрос из состояния собирает knowledge_request.build_query.
Поэтому его можно гонять напрямую на размеченных запросах (eval/retrieval) с разными
конфигурациями стадий — без Extractor, Policy и LLM.

Пайплайн — композиция стадий. Стадия — чистая функция (Candidates, Ctx) -> Candidates;
конфигурация = кортеж стадий. Эксперимент = другой кортеж или другие параметры.

    parse      сниппеты → нормализованный запрос (тексты, области, бусты, бюджет)
    by_id      IdSnippet → evidence(origin="by_id")              ChunkStore.get
    scope      ScopeSnippet → allowed chunk ids                  ChunkStore.where
    recall     каждый TextSnippet(role="query"): BM25 ∥ dense    LexicalIndex, Embedder+VectorIndex
    fuse       RRF (k=60) по всем спискам с весами сниппетов → top recall_k
    boost      BoostSnippet: score *= factor для разделов области
    rerank     Reranker → top_k; below_floor, если лучший < min_rerank_score
    pin        PinSnippet → evidence(origin="pinned")            ChunkStore.where
    fallback   recall+fuse+rerank без области → fallback_k, origin="fallback"
    pack       дедупликация, порядок by_id → pinned → ranked → fallback, бюджет max_tokens;
               scope_mass — доля реранка по областям (→ SoftSignal)

Стадии живут в application/retrieval_stages.py, хелперы (нормализация текста,
RRF) — там же. Деградация: reranker=None → ранжирование по fused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from energytool.application.build_index import KnowledgeIndex
from energytool.application.ports import Embedder, Reranker
from energytool.domain.knowledge import Evidence, KnowledgeQuery, KnowledgeResult


@dataclass(slots=True)
class Candidates:
    """Промежуточное состояние пайплайна."""

    allowed: frozenset[str] | None = None                                     # None = вся база
    lists: dict[str, list[tuple[str, float]]] = field(default_factory=dict)   # "bm25:0", "dense:0" → ранжирование
    fused: list[tuple[str, float]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    below_floor: bool = False


@dataclass(frozen=True, slots=True)
class Ctx:
    query: KnowledgeQuery
    index: KnowledgeIndex
    embedder: Embedder
    reranker: Reranker | None
    rrf_k: int
    timings_ms: dict[str, float]


Stage = Callable[[Candidates, Ctx], Candidates]


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    name: str
    stages: tuple[Stage, ...]          # по умолчанию retrieval_stages.DEFAULT
    rrf_k: int = 60


@dataclass(frozen=True, slots=True)
class RetrieveKnowledge:
    config: PipelineConfig
    index: KnowledgeIndex
    embedder: Embedder
    reranker: Reranker | None = None

    def __call__(self, query: KnowledgeQuery) -> KnowledgeResult:
        """Прогнать стадии config.stages, замерить каждую в timings_ms, собрать KnowledgeResult
        (trace: списки по стадиям для отладки и eval)."""
        raise NotImplementedError
