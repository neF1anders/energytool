"""Сборка KnowledgeQuery из состояния диалога. Чистая функция, отделена от пайплайна поиска:
RetrieveKnowledge не знает про лидеров и кандидатов, знает только сниппеты.

    build_query(extraction, state, decision, budget, restrict_scope=True) -> KnowledgeQuery

Правила:
    extraction.search_query          → TextSnippet(weight=1.0)
    последняя реплика клиента         → TextSnippet(weight=0.5)
    до 3 предыдущих реплик клиента   → TextSnippet(weight=0.5), не более 1200 символов
    фото в контексте/collection_method → product photo добавляется только в retrieval scope
    кандидаты board ∪ mentions ∪ {general, energy} → ScopeSnippet
    лидер                            → BoostSnippet(leader, 1.2)
                                       + PinSnippet(leader, {forbidden, not_fits})
    decision.pinned_sections         → IdSnippet
    need_facts=False                 → только IdSnippet — без поиска и моделей
    бюджет (с min_rerank_score = Thresholds.rerank_floor) → BudgetSnippet

Батч этапа 1 (AnswerQuestion) использует ту же функцию.
"""

from __future__ import annotations

from collections.abc import Sequence

from energytool.domain.extraction import Extraction
from energytool.domain.knowledge import (
    BoostSnippet,
    BudgetSnippet,
    IdSnippet,
    KnowledgeQuery,
    PinSnippet,
    Scope,
    ScopeSnippet,
    SectionKind,
    Snippet,
    TextSnippet,
    Topic,
    scope_for,
)
from energytool.domain.policy import PolicyDecision
from energytool.domain.products import Product
from energytool.domain.state import ConversationState, Message, Role

LEADER_BOOST = 1.2
LEADER_PIN_KINDS = frozenset({SectionKind.FORBIDDEN, SectionKind.NOT_FITS})
ALWAYS_IN_SCOPE: frozenset[Scope] = frozenset({Topic.GENERAL, Topic.ENERGY})
HISTORY_USER_MESSAGES = 3
HISTORY_TEXT_LIMIT = 1_200
PHOTO_COLLECTION_MARKERS = ("фото", "фотограф", "снимк")


def _bounded_text(parts: Sequence[str], limit: int = HISTORY_TEXT_LIMIT) -> str:
    """Последние факты диалога в фиксированном символьном бюджете."""
    text = " ".join(part.strip() for part in parts if part.strip())
    if len(text) <= limit:
        return text
    return text[-limit:].lstrip()


def _collection_method(state: ConversationState) -> str:
    value = state.value("collection_method")
    return value if isinstance(value, str) else ""


def _mentions_photo_collection(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PHOTO_COLLECTION_MARKERS)


def build_query(
    extraction: Extraction,
    state: ConversationState,
    decision: PolicyDecision,
    budget: BudgetSnippet,
    *,
    restrict_scope: bool = True,
) -> KnowledgeQuery:
    """Сборка по правилам из docstring модуля.

    При ``restrict_scope=False`` область не сужается, но лидер по-прежнему бустится.
    """
    snippets: list[Snippet] = []
    if decision.pinned_sections:
        snippets.append(IdSnippet(decision.pinned_sections))
    if not decision.need_facts:
        return (*snippets, budget)
    user_messages = [m.text for m in state.history if m.role is Role.USER]
    last_user = user_messages[-1] if user_messages else ""
    main = extraction.search_query or last_user
    snippets.append(TextSnippet(main, weight=1.0))
    if last_user and last_user != main:
        snippets.append(TextSnippet(last_user, weight=0.5))
    collection_method = _collection_method(state)
    scenario_parts = user_messages[-(HISTORY_USER_MESSAGES + 1) : -1]
    if collection_method and not any(collection_method in part for part in scenario_parts):
        scenario_parts.append(f"Способ сбора показаний: {collection_method}")
    scenario_context = _bounded_text(scenario_parts)
    if scenario_context:
        snippets.append(TextSnippet(scenario_context, weight=0.5))
        assistant_messages = [m.text for m in state.history if m.role is Role.ASSISTANT]
        rerank_context = _bounded_text([*assistant_messages[-1:], scenario_context])
        snippets.append(TextSnippet(rerank_context, role="context"))
    mentioned = tuple(dict.fromkeys(extraction.mentions))
    retrieval_products = set(decision.candidates) | set(mentioned)
    photo_context = " ".join((main, last_user, scenario_context, collection_method))
    if _mentions_photo_collection(photo_context):
        retrieval_products.add(Product.PHOTO)
    if restrict_scope and retrieval_products:
        scopes = {scope_for(p) for p in retrieval_products} | ALWAYS_IN_SCOPE
        snippets.append(ScopeSnippet(frozenset(scopes)))
    retrieval_leader = decision.leader
    if mentioned and retrieval_leader not in mentioned:
        retrieval_leader = mentioned[0] if len(mentioned) == 1 else None
    if retrieval_leader is not None:
        leader_scope = scope_for(retrieval_leader)
        snippets.append(BoostSnippet(leader_scope, LEADER_BOOST))
        snippets.append(PinSnippet(leader_scope, LEADER_PIN_KINDS, limit=2))
    snippets.append(budget)
    return tuple(snippets)


def query_from_history(
    messages: Sequence[Message], budget: BudgetSnippet | None = None
) -> KnowledgeQuery:
    """Запрос без Extractor (наивный путь этапа 1 и деградация): последняя реплика
    клиента — основной текст, предыдущие реплики клиента — второй текст с весом 0.5,
    последний вопрос ассистента — контекст для реранкера."""
    user = [m.text for m in messages if m.role is Role.USER]
    snippets: list[Snippet] = []
    if user:
        snippets.append(TextSnippet(user[-1], weight=1.0))
    if len(user) > 1:
        snippets.append(TextSnippet(" ".join(user[:-1]), weight=0.5))
        assistant = [m.text for m in messages if m.role is Role.ASSISTANT]
        if assistant:
            snippets.append(TextSnippet(assistant[-1], role="context"))
    snippets.append(budget or BudgetSnippet())
    return tuple(snippets)
