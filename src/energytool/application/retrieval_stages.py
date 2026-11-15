"""Стадии пайплайна RetrieveKnowledge и готовые конфигурации (spec §7.4).

Стадия — функция (Candidates, Ctx) -> Candidates. Конфигурация — кортеж стадий плюс
параметры. Эксперимент = другая конфигурация, use case не меняется.

    by_id → scope → recall → fuse → boost → rerank → pin → fallback → pack

Деградации (каждая пишется в Candidates.notes и в лог):
- эмбеддер недоступен → recall только BM25 (dense_failed);
- реранкер недоступен или выключен → ранжирование по RRF (rerank_failed / no_reranker);
- fallback добавляет раздел вне области, только если он прошёл бы в top_k основного
  ранжирования: иначе страховка от ошибки роутинга превращается в шум для ответа.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from energytool.application.build_index import KnowledgeIndex
from energytool.application.ports import Embedder, ExternalServiceError, Reranker
from energytool.domain.knowledge import (
    BoostSnippet,
    BudgetSnippet,
    Chunk,
    Evidence,
    IdSnippet,
    KnowledgeQuery,
    Origin,
    PinSnippet,
    Scope,
    ScopeSnippet,
    TextSnippet,
)

logger = logging.getLogger(__name__)

RRF_K = 60
FALLBACK_POOL = 10  # кандидатов вне области, которые отдаём реранкеру в fallback


# --- запрос ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    texts: tuple[TextSnippet, ...]
    context: tuple[str, ...]
    scopes: frozenset[Scope] | None
    boosts: Mapping[Scope, float]
    pins: tuple[PinSnippet, ...]
    ids: tuple[str, ...]
    budget: BudgetSnippet

    @property
    def rerank_query(self) -> str:
        """Главный текст (наибольший вес) + контекст: реранкер видит ситуацию клиента."""
        if not self.texts:
            return ""
        main = max(self.texts, key=lambda t: t.weight).text
        return "\n".join((main, *self.context))


def parse_query(query: KnowledgeQuery) -> ParsedQuery:
    texts: list[TextSnippet] = []
    context: list[str] = []
    scopes: set[Scope] = set()
    has_scope = False
    boosts: dict[Scope, float] = {}
    pins: list[PinSnippet] = []
    ids: list[str] = []
    budget = BudgetSnippet()
    for snippet in query:
        if isinstance(snippet, TextSnippet):
            if not snippet.text.strip():
                continue
            if snippet.role == "context":
                context.append(snippet.text)
            else:
                texts.append(snippet)
        elif isinstance(snippet, ScopeSnippet):
            has_scope = True
            scopes |= snippet.scopes
        elif isinstance(snippet, BoostSnippet):
            boosts[snippet.scope] = boosts.get(snippet.scope, 1.0) * snippet.factor
        elif isinstance(snippet, PinSnippet):
            pins.append(snippet)
        elif isinstance(snippet, IdSnippet):
            ids.extend(snippet.ids)
        else:
            budget = snippet
    return ParsedQuery(
        texts=tuple(texts),
        context=tuple(context),
        scopes=frozenset(scopes) if has_scope else None,
        boosts=boosts,
        pins=tuple(pins),
        ids=tuple(dict.fromkeys(ids)),
        budget=budget,
    )


# --- состояние пайплайна ---------------------------------------------------------------------


Ranking = list[tuple[str, float]]


@dataclass(slots=True)
class Candidates:
    """Промежуточное состояние пайплайна."""

    allowed: frozenset[str] | None = None  # None = вся база
    query_vectors: list[list[float]] | None = None  # кэш для fallback
    lists: dict[str, Ranking] = field(default_factory=dict)  # "bm25:0", "dense:0" → ранжирование
    fused: Ranking = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    below_floor: bool = False
    notes: list[str] = field(default_factory=list)
    packed: tuple[Evidence, ...] = ()
    scope_mass: dict[Scope, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Ctx:
    parsed: ParsedQuery
    index: KnowledgeIndex
    embedder: Embedder | None
    reranker: Reranker | None
    rrf_k: int = RRF_K


Stage = Callable[[Candidates, Ctx], Candidates]


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    name: str
    stages: tuple[Stage, ...]
    rrf_k: int = RRF_K
    use_reranker: bool = True
    use_embedder: bool = True


# --- стадии ----------------------------------------------------------------------------------


def by_id(c: Candidates, ctx: Ctx) -> Candidates:
    for chunk in ctx.index.chunks.get(ctx.parsed.ids):
        c.evidence.append(Evidence(chunk=chunk, score=1.0, origin="by_id"))
    return c


def scope(c: Candidates, ctx: Ctx) -> Candidates:
    if ctx.parsed.scopes is not None:
        chunks = ctx.index.chunks.where(scopes=ctx.parsed.scopes)
        c.allowed = frozenset(chunk.id for chunk in chunks)
    return c


def make_recall(*, lexical: bool = True, dense: bool = True) -> Stage:
    def recall(c: Candidates, ctx: Ctx) -> Candidates:
        c.lists.update(_recall_lists(c, ctx, c.allowed, lexical=lexical, dense=dense))
        return c

    return recall


def fuse(c: Candidates, ctx: Ctx) -> Candidates:
    c.fused = _rrf(c.lists, ctx)[: ctx.parsed.budget.recall_k]
    return c


def boost(c: Candidates, ctx: Ctx) -> Candidates:
    if ctx.parsed.boosts and c.fused:
        c.fused = _boosted(c.fused, ctx)
    return c


def rerank(c: Candidates, ctx: Ctx) -> Candidates:
    if not c.fused:
        return c
    budget = ctx.parsed.budget
    chunks = _chunks_by_id(ctx, [chunk_id for chunk_id, _ in c.fused])
    ranked = _rerank_or_fused(c, ctx, chunks, c.fused, budget.top_k)
    if ctx.reranker is not None and "rerank_failed" not in c.notes:
        ranked = _drop_weak(ranked, budget)
    c.evidence.extend(_evidence(chunks, ranked, "ranked", c.fused))
    if budget.min_rerank_score is not None and ctx.reranker is not None and ranked:
        c.below_floor = "rerank_failed" not in c.notes and ranked[0][1] < budget.min_rerank_score
    return c


def pin(c: Candidates, ctx: Ctx) -> Candidates:
    for snippet in ctx.parsed.pins:
        chunks = ctx.index.chunks.where(scopes=frozenset({snippet.scope}), kinds=snippet.kinds)
        for chunk in list(chunks)[: snippet.limit]:
            c.evidence.append(Evidence(chunk=chunk, score=1.0, origin="pinned"))
    return c


def make_fallback(*, lexical: bool = True, dense: bool = True) -> Stage:
    def fallback(c: Candidates, ctx: Ctx) -> Candidates:
        budget = ctx.parsed.budget
        if c.allowed is None or budget.fallback_k <= 0 or not ctx.parsed.texts:
            return c
        lists = _recall_lists(c, ctx, None, lexical=lexical, dense=dense)
        outside = [(i, s) for i, s in _rrf(lists, ctx) if i not in c.allowed][:FALLBACK_POOL]
        if not outside:
            return c
        chunks = _chunks_by_id(ctx, [chunk_id for chunk_id, _ in outside])
        ranked = _rerank_or_fused(c, ctx, chunks, outside, budget.fallback_k)
        floor = min((e.score for e in c.evidence if e.origin == "ranked"), default=float("-inf"))
        accepted = [(i, s) for i, s in ranked if s >= floor]
        c.evidence.extend(_evidence(chunks, accepted, "fallback", outside))
        if accepted:
            c.notes.append(f"fallback_added:{len(accepted)}")
        return c

    return fallback


_ORIGIN_ORDER: dict[Origin, int] = {
    "by_id": 0,
    "pinned": 1,
    "hint": 1,
    "ranked": 2,
    "fallback": 3,
    "context": 4,
    "topic": 4,
}


def pack(c: Candidates, ctx: Ctx) -> Candidates:
    ordered = sorted(c.evidence, key=lambda e: _ORIGIN_ORDER[e.origin])
    seen: set[str] = set()
    packed: list[Evidence] = []
    tokens = 0
    for evidence in ordered:
        if evidence.chunk.id in seen:
            continue
        if packed and tokens + evidence.chunk.tokens > ctx.parsed.budget.max_tokens:
            c.notes.append(f"budget_cut:{evidence.chunk.id}")
            continue
        seen.add(evidence.chunk.id)
        packed.append(evidence)
        tokens += evidence.chunk.tokens
    c.packed = tuple(packed)
    c.scope_mass = _scope_mass(e for e in packed if e.origin == "ranked")
    return c


# --- конфигурации ----------------------------------------------------------------------------


def _stages(*, lexical: bool, dense: bool) -> tuple[Stage, ...]:
    return (
        by_id,
        scope,
        make_recall(lexical=lexical, dense=dense),
        fuse,
        boost,
        rerank,
        pin,
        make_fallback(lexical=lexical, dense=dense),
        pack,
    )


CONFIGS: dict[str, PipelineConfig] = {
    "hybrid_rerank": PipelineConfig("hybrid_rerank", _stages(lexical=True, dense=True)),
    "hybrid": PipelineConfig("hybrid", _stages(lexical=True, dense=True), use_reranker=False),
    "bm25_rerank": PipelineConfig(
        "bm25_rerank", _stages(lexical=True, dense=False), use_embedder=False
    ),
    "bm25": PipelineConfig(
        "bm25", _stages(lexical=True, dense=False), use_reranker=False, use_embedder=False
    ),
    "dense_rerank": PipelineConfig("dense_rerank", _stages(lexical=False, dense=True)),
    "dense": PipelineConfig("dense", _stages(lexical=False, dense=True), use_reranker=False),
}
DEFAULT_CONFIG = "hybrid_rerank"


class UnknownPipelineConfigError(KeyError):
    """RETRIEVAL_CONFIG не совпадает ни с одной конфигурацией."""


def get_config(name: str) -> PipelineConfig:
    try:
        return CONFIGS[name]
    except KeyError as exc:
        raise UnknownPipelineConfigError(f"{name!r}; доступны: {', '.join(CONFIGS)}") from exc


# --- помощники -------------------------------------------------------------------------------


def _recall_lists(
    c: Candidates,
    ctx: Ctx,
    allowed: frozenset[str] | None,
    *,
    lexical: bool,
    dense: bool,
) -> dict[str, Ranking]:
    texts = ctx.parsed.texts
    k = ctx.parsed.budget.recall_k
    lists: dict[str, Ranking] = {}
    if allowed is not None and not allowed:
        return lists
    if lexical:
        for i, snippet in enumerate(texts):
            lists[f"bm25:{i}"] = ctx.index.lexical.search(snippet.text, k, allowed)
    vectors = _query_vectors(c, ctx) if dense else None
    if vectors is not None:
        for i, vector in enumerate(vectors):
            lists[f"dense:{i}"] = ctx.index.vectors.search(vector, k, allowed)
    return lists


def _query_vectors(c: Candidates, ctx: Ctx) -> list[list[float]] | None:
    if c.query_vectors is not None:
        return c.query_vectors
    if not ctx.index.dense_ready or ctx.embedder is None or not ctx.parsed.texts:
        return None
    if "dense_failed" in c.notes:
        return None
    try:
        c.query_vectors = ctx.embedder.embed([t.text for t in ctx.parsed.texts])
    except ExternalServiceError as exc:
        c.notes.append("dense_failed")
        logger.warning("dense recall degraded to bm25: %s", exc)
        return None
    return c.query_vectors


def _rrf(lists: Mapping[str, Ranking], ctx: Ctx) -> Ranking:
    weights = {i: snippet.weight for i, snippet in enumerate(ctx.parsed.texts)}
    scores: dict[str, float] = {}
    for key, ranking in lists.items():
        weight = weights.get(int(key.split(":")[1]), 1.0)
        for rank, (chunk_id, _) in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (ctx.rrf_k + rank + 1)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def _boosted(ranking: Ranking, ctx: Ctx) -> Ranking:
    chunks = _chunks_by_id(ctx, [chunk_id for chunk_id, _ in ranking])
    boosted = [
        (chunk_id, score * ctx.parsed.boosts.get(chunks[chunk_id].scope, 1.0))
        for chunk_id, score in ranking
        if chunk_id in chunks
    ]
    return sorted(boosted, key=lambda pair: pair[1], reverse=True)


def _rerank_or_fused(
    c: Candidates, ctx: Ctx, chunks: Mapping[str, Chunk], pool: Ranking, top_n: int
) -> Ranking:
    if ctx.reranker is None:
        if "no_reranker" not in c.notes:
            c.notes.append("no_reranker")
        return pool[:top_n]
    ids = [chunk_id for chunk_id, _ in pool if chunk_id in chunks]
    try:
        results = ctx.reranker.rerank(
            ctx.parsed.rerank_query, [chunks[i].text for i in ids], len(ids)
        )
    except ExternalServiceError as exc:
        c.notes.append("rerank_failed")
        logger.warning("rerank degraded to fused ranking: %s", exc)
        return pool[:top_n]
    ranked = [(ids[index], score) for index, score in results]
    if ctx.parsed.boosts:
        ranked = _boosted(ranked, ctx)
    return ranked[:top_n]


def _drop_weak(ranked: Ranking, budget: BudgetSnippet) -> Ranking:
    """Слабые разделы приносят в ответ посторонние цифры: оставляем только близкие к
    лучшему по скору реранкера. Скоры RRF для этого не годятся — вызывать после реранка."""
    if not ranked:
        return ranked
    floor = max(budget.keep_floor, budget.keep_ratio * ranked[0][1])
    return [ranked[0], *(pair for pair in ranked[1:] if pair[1] >= floor)]


def _evidence(
    chunks: Mapping[str, Chunk], ranked: Ranking, origin: Origin, fused: Ranking
) -> list[Evidence]:
    fused_scores = dict(fused)
    return [
        Evidence(
            chunk=chunks[chunk_id],
            score=score,
            origin=origin,
            stage_scores={"rrf": round(fused_scores.get(chunk_id, 0.0), 5), "final": score},
        )
        for chunk_id, score in ranked
    ]


def _chunks_by_id(ctx: Ctx, ids: Sequence[str]) -> dict[str, Chunk]:
    return {chunk.id: chunk for chunk in ctx.index.chunks.get(ids)}


def _scope_mass(evidence: Iterable[Evidence]) -> dict[Scope, float]:
    mass: dict[Scope, float] = {}
    for item in evidence:
        mass[item.chunk.scope] = mass.get(item.chunk.scope, 0.0) + max(item.score, 0.0)
    total = sum(mass.values())
    return {s: round(v / total, 3) for s, v in mass.items()} if total > 0 else {}
