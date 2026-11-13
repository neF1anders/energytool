"""Сборка KnowledgeQuery из состояния диалога. Чистая функция, отделена от пайплайна поиска:
RetrieveKnowledge не знает про лидеров и кандидатов, знает только сниппеты.

    build_query(extraction, state, decision, budget, restrict_scope=True) -> KnowledgeQuery

Правила:
    extraction.search_query          → TextSnippet(weight=1.0)
    последняя реплика клиента         → TextSnippet(weight=0.5)
    кандидаты board ∪ {general, energy} → ScopeSnippet   (нет кандидатов — без области)
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
from energytool.domain.state import ConversationState, Message, Role

LEADER_BOOST = 1.2
LEADER_PIN_KINDS = frozenset({SectionKind.FORBIDDEN, SectionKind.NOT_FITS})
ALWAYS_IN_SCOPE: frozenset[Scope] = frozenset({Topic.GENERAL, Topic.ENERGY})


def build_query(
    extraction: Extraction,
    state: ConversationState,
    decision: PolicyDecision,
    budget: BudgetSnippet,
    *,
    restrict_scope: bool = True,
) -> KnowledgeQuery:
    """Сборка по правилам из docstring модуля. restrict_scope=False (батч: один вопрос без
    накопленного контекста) — область не сужается, лидер только бустится."""
    snippets: list[Snippet] = []
    if decision.pinned_sections:
        snippets.append(IdSnippet(decision.pinned_sections))
    if not decision.need_facts:
        return (*snippets, budget)
    last_user = next((m.text for m in reversed(state.history) if m.role is Role.USER), "")
    main = extraction.search_query or last_user
    snippets.append(TextSnippet(main, weight=1.0))
    if last_user and last_user != main:
        snippets.append(TextSnippet(last_user, weight=0.5))
    if restrict_scope and decision.candidates:
        scopes = {scope_for(p) for p in decision.candidates} | ALWAYS_IN_SCOPE
        snippets.append(ScopeSnippet(frozenset(scopes)))
    if decision.leader is not None:
        leader_scope = scope_for(decision.leader)
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
