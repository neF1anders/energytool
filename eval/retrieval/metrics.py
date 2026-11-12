"""Метрики ретрива по набору случаев."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    ranked: tuple[str, ...]  # id разделов по порядку
    expected: frozenset[str]
    expected_scopes: frozenset[str]
    top_scope: str | None
    ms: float

    def rank(self) -> int | None:
        """Позиция первого ожидаемого раздела, с 1."""
        return next((i for i, cid in enumerate(self.ranked, 1) if cid in self.expected), None)


@dataclass(frozen=True, slots=True)
class Summary:
    n: int
    hit_at_1: float
    recall_at_4: float
    mrr_at_10: float
    scope_at_1: float
    p50_ms: float
    max_ms: float


def summarize(results: Sequence[CaseResult]) -> Summary:
    n = len(results) or 1
    ranks = [r.rank() for r in results]
    return Summary(
        n=len(results),
        hit_at_1=sum(1 for k in ranks if k == 1) / n,
        recall_at_4=sum(1 for k in ranks if k is not None and k <= 4) / n,
        mrr_at_10=sum(1 / k for k in ranks if k is not None and k <= 10) / n,
        scope_at_1=sum(1 for r in results if r.top_scope in r.expected_scopes) / n,
        p50_ms=median([r.ms for r in results]) if results else 0.0,
        max_ms=max((r.ms for r in results), default=0.0),
    )
