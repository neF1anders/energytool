"""Знания из базы: разделы базы, запрос к поиску и найденные фрагменты.

Поиск — use case application/retrieve_knowledge.py:
    RetrieveKnowledge(query: KnowledgeQuery) -> KnowledgeResult
где KnowledgeQuery — кортеж разнородных сниппетов: тексты, область поиска, бусты,
закрепления, выборка по id, бюджет. Use case не знает про диалог и состояние:
запрос из состояния собирает отдельная функция application/knowledge_request.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from energytool.domain.products import Product


class SectionKind(StrEnum):
    """Тип раздела базы по заголовку. Проставляется при индексации."""

    FAQ = "faq"
    FITS = "fits"  # «Кому подходит», «Подходит, если»
    NOT_FITS = "not_fits"  # «Не подходит, если»
    ALLOWED_FACTS = "allowed_facts"  # «Факты, которые можно утверждать»
    FORBIDDEN = "forbidden"  # «Чего утверждать нельзя»
    WHAT_WE_DO = "what_we_do"  # «Что делает МТС», «Что мы не делаем»
    NUMBERS = "numbers"  # 01-numbers-and-indicators
    REQUIREMENTS = "requirements"  # 03-calculation-and-requests
    SPECIALIST = "specialist"  # 02 › «Когда требуется специалист»
    OTHER = "other"


class Topic(StrEnum):
    """Метка раздела, не являющегося продуктом."""

    GENERAL = "general"  # 26-general, 20-portfolio, 00-reference
    ENERGY = "energy"  # 10-energy без привязки к продукту


Scope = Product | Topic


def scope_for(product: Product) -> Scope:
    """Область базы для продукта. Оба типа солнечных станций описаны в одном файле
    (23-solar), поэтому гибрид ищется в области SOLAR_GRID."""
    return Product.SOLAR_GRID if product is Product.SOLAR_HYBRID else product


# --- раздел базы ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """Раздел базы (## или ###) — единица поиска."""

    id: str  # "20-products/21-orem.md#кому-подходит/порог-доступа"
    file: str  # "20-products/21-orem.md"
    path: str  # «Выход на ОРЭМ › Кому подходит › Порог доступа»
    text: str  # путь заголовков + тело раздела
    scope: Scope
    kind: SectionKind
    tokens: int = 0


# --- запрос: кортеж сниппетов -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextSnippet:
    """Текст для поиска. Несколько текстов ищутся раздельно и сливаются RRF с весами."""

    text: str
    weight: float = 1.0
    role: Literal["query", "context"] = "query"  # context — только для реранкера


@dataclass(frozen=True, slots=True)
class ScopeSnippet:
    """Область поиска: WHERE scope IN scopes. Нет сниппета — вся база."""

    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class BoostSnippet:
    """Мягкий буст разделов области: score *= factor."""

    scope: Scope
    factor: float


@dataclass(frozen=True, slots=True)
class PinSnippet:
    """Закрепить разделы вне ранжирования, например карточку лидера:
    scope=orem, kinds={forbidden, not_fits}."""

    scope: Scope
    kinds: frozenset[SectionKind]
    limit: int = 2


@dataclass(frozen=True, slots=True)
class IdSnippet:
    """Выборка разделов по id, без поиска."""

    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BudgetSnippet:
    top_k: int = 4  # ранжированных разделов в ответе
    recall_k: int = 20  # кандидатов до реранкера
    fallback_k: int = 2  # top-k по всей базе без области — страховка роутинга
    max_tokens: int = 2500
    min_rerank_score: float | None = None
    # отсечение слабых разделов после реранкера: остаются разделы со скором не ниже
    # max(keep_floor, keep_ratio · лучший скор); лучший раздел остаётся всегда
    keep_ratio: float = 0.0
    keep_floor: float = 0.0


Snippet = TextSnippet | ScopeSnippet | BoostSnippet | PinSnippet | IdSnippet | BudgetSnippet
KnowledgeQuery = tuple[Snippet, ...]


# --- результат --------------------------------------------------------------------------------

# hint — раздел «какие сведения могут понадобиться»: подсказка для уточняющего вопроса;
# context — сосед найденного раздела по документу (application/context_expansion.py);
# topic — раздел, найденный по предыдущим репликам клиента в диалоге
Origin = Literal["ranked", "pinned", "fallback", "by_id", "hint", "context", "topic"]


@dataclass(frozen=True, slots=True)
class Evidence:
    """Найденный раздел, на который можно опираться в ответе."""

    chunk: Chunk
    score: float  # реранк; 1.0 для закреплённых и by_id
    origin: Origin
    stage_scores: Mapping[str, float] = field(default_factory=dict)  # bm25, dense, rrf, rerank


@dataclass(frozen=True, slots=True)
class KnowledgeResult:
    evidence: tuple[Evidence, ...]
    below_floor: bool = False  # лучший реранк ниже min_rerank_score
    scope_mass: Mapping[Scope, float] = field(
        default_factory=dict
    )  # доля реранка по областям → SoftSignal
    timings_ms: Mapping[str, float] = field(default_factory=dict)
    trace: Mapping[str, object] = field(default_factory=dict)
