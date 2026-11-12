"""Use case RetrieveKnowledge: поиск по базе знаний.

    RetrieveKnowledge(query: KnowledgeQuery) -> KnowledgeResult

Вход — кортеж сниппетов (domain/knowledge.py), выход — Evidence в терминах домена.
Use case не знает про диалог: запрос из состояния собирает knowledge_request.build_query.
Поэтому его можно гонять напрямую на размеченных запросах (eval/retrieval) с разными
конфигурациями стадий — без Extractor, Policy и LLM.

Стадии, конфигурации и деградации — application/retrieval_stages.py.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from energytool.application.build_index import KnowledgeIndex
from energytool.application.ports import Embedder, Reranker
from energytool.application.retrieval_stages import (
    Candidates,
    Ctx,
    PipelineConfig,
    parse_query,
)
from energytool.domain.knowledge import KnowledgeQuery, KnowledgeResult

logger = logging.getLogger(__name__)

_TRACE_TOP = 5


@dataclass(frozen=True, slots=True)
class RetrieveKnowledge:
    config: PipelineConfig
    index: KnowledgeIndex
    embedder: Embedder | None = None
    reranker: Reranker | None = None

    def __call__(self, query: KnowledgeQuery) -> KnowledgeResult:
        started = time.perf_counter()
        parsed = parse_query(query)
        ctx = Ctx(
            parsed=parsed,
            index=self.index,
            embedder=self.embedder if self.config.use_embedder else None,
            reranker=self.reranker if self.config.use_reranker else None,
            rrf_k=self.config.rrf_k,
        )
        candidates = Candidates()
        timings: dict[str, float] = {}
        for stage in self.config.stages:
            stage_started = time.perf_counter()
            candidates = stage(candidates, ctx)
            timings[stage.__name__] = _ms_since(stage_started)
            logger.debug(
                "retrieval stage",
                extra={"data": {"stage": stage.__name__, **_stage_snapshot(candidates)}},
            )
        timings["total"] = _ms_since(started)
        result = KnowledgeResult(
            evidence=candidates.packed,
            below_floor=candidates.below_floor,
            scope_mass=candidates.scope_mass,
            timings_ms=timings,
            trace={
                "config": self.config.name,
                "notes": list(candidates.notes),
                "lists": {k: v[:_TRACE_TOP] for k, v in candidates.lists.items()},
                "fused": candidates.fused[:_TRACE_TOP],
            },
        )
        logger.info(
            "retrieval done",
            extra={
                "data": {
                    "config": self.config.name,
                    "query": parsed.rerank_query[:200],
                    "evidence": [
                        (e.chunk.id, e.origin, round(e.score, 3)) for e in result.evidence
                    ],
                    "below_floor": result.below_floor,
                    "notes": candidates.notes,
                    "ms": timings["total"],
                }
            },
        )
        return result


def _stage_snapshot(c: Candidates) -> dict[str, object]:
    return {
        "allowed": None if c.allowed is None else len(c.allowed),
        "lists": {k: len(v) for k, v in c.lists.items()},
        "fused_top": [chunk_id for chunk_id, _ in c.fused[:3]],
        "evidence": len(c.evidence),
        "notes": list(c.notes),
    }


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
