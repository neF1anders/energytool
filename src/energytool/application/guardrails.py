"""Запрос разделов-ограничений для ответа: что нельзя утверждать и когда нужен специалист.

    guard_query(scope_mass) -> KnowledgeQuery

База содержит разделы с правилами консультации («Общие границы», «Чего утверждать нельзя»,
«Когда требуется специалист») и списки сведений, которые нужны для расчёта по каждому
продукту (из них получается уместный уточняющий вопрос). Поиск по вопросу клиента их
обычно не находит: клиент спрашивает про цену или гарантию, а не про ограничения.
Поэтому они подкладываются в ответ всегда: общие — по id, продуктовые — для продуктов,
которые нашёл поиск.

Запрос без текста: RetrieveKnowledge выполняет только by_id и pin, модели не вызываются.
"""

from __future__ import annotations

from collections.abc import Mapping

from energytool.domain.knowledge import (
    BudgetSnippet,
    IdSnippet,
    KnowledgeQuery,
    Origin,
    PinSnippet,
    Scope,
    SectionKind,
    Snippet,
)
from energytool.domain.products import Product

LIMITS_FILE = "00-reference/02-product-scope-and-limits.md"
REQUESTS_FILE = "00-reference/03-calculation-and-requests.md"
GENERAL_GUARD_IDS: tuple[str, ...] = (
    f"{LIMITS_FILE}#общие-границы",
    f"{LIMITS_FILE}#когда-требуется-специалист",
    "20-products/20-portfolio.md#чего-утверждать-нельзя",
    f"{REQUESTS_FILE}#обратный-звонок",
)
# продукт не определён — общий список сведений для первичного понимания задачи
GENERAL_HINT_ID = (
    f"{REQUESTS_FILE}#какие-сведения-могут-понадобиться/для-первичного-понимания-задачи"
)
PRODUCT_LIMIT_IDS: Mapping[Scope, str] = {
    Product.OREM: f"{LIMITS_FILE}#выход-на-оптовый-рынок",
    Product.METERING: f"{LIMITS_FILE}#облачная-платформа-учёта",
    Product.SOLAR_GRID: f"{LIMITS_FILE}#солнечные-электростанции",
    Product.LOSSES: f"{LIMITS_FILE}#поиск-коммерческих-потерь",
    Product.PHOTO: f"{LIMITS_FILE}#распознавание-показаний-по-фотографии",
}
GUARD_KINDS = frozenset({SectionKind.FORBIDDEN, SectionKind.REQUIREMENTS})
MIN_SCOPE_MASS = 0.25  # доля продукта в найденных разделах, с которой берём его ограничения
MAX_GUARD_PRODUCTS = 2
GUARD_BUDGET = BudgetSnippet(top_k=0, recall_k=0, fallback_k=0, max_tokens=6000)


def guard_origin(chunk_id: str) -> Origin:
    """Куда раздел попадёт в промпте: подсказка для уточняющего вопроса или ограничение."""
    is_hint = chunk_id.startswith(f"{REQUESTS_FILE}#какие-сведения-могут-понадобиться")
    return "hint" if is_hint else "pinned"


def guard_query(scope_mass: Mapping[Scope, float]) -> KnowledgeQuery:
    """Общие ограничения + ограничения продуктов, преобладающих в найденных разделах."""
    products = sorted(
        (scope for scope, mass in scope_mass.items() if scope in PRODUCT_LIMIT_IDS),
        key=lambda scope: scope_mass[scope],
        reverse=True,
    )
    chosen = [s for s in products if scope_mass[s] >= MIN_SCOPE_MASS][:MAX_GUARD_PRODUCTS]
    ids = [*GENERAL_GUARD_IDS, *(PRODUCT_LIMIT_IDS[s] for s in chosen)]
    if not chosen:
        ids.append(GENERAL_HINT_ID)
    snippets: list[Snippet] = [IdSnippet(tuple(ids))]
    snippets.extend(PinSnippet(scope, GUARD_KINDS, limit=2) for scope in chosen)
    snippets.append(GUARD_BUDGET)
    return tuple(snippets)
