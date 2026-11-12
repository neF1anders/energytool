"""Сборка KnowledgeQuery из состояния диалога. Чистая функция, отделена от пайплайна поиска:
RetrieveKnowledge не знает про лидеров и кандидатов, знает только сниппеты.

    build_query(extraction, state, board, decision, thresholds) -> KnowledgeQuery

Правила:
    extraction.search_query          → TextSnippet(weight=1.0)
    последняя реплика клиента         → TextSnippet(weight=0.5)
    кандидаты board ∪ {general, energy} → ScopeSnippet   (нет кандидатов — без области)
    лидер                            → BoostSnippet(leader, 1.2)
                                       + PinSnippet(leader, {forbidden, not_fits})
    decision.pinned_sections         → IdSnippet
    need_facts=False                 → только IdSnippet — без поиска и моделей
    thresholds.rerank_floor, бюджет  → BudgetSnippet

Батч этапа 1 (AnswerQuestion) использует ту же функцию.
"""

from __future__ import annotations

from collections.abc import Sequence

from energytool.domain.extraction import Extraction
from energytool.domain.knowledge import BudgetSnippet, KnowledgeQuery, Snippet, TextSnippet
from energytool.domain.policy import PolicyDecision, Thresholds
from energytool.domain.scoring import ScoreBoard
from energytool.domain.state import ConversationState, Message, Role


def build_query(
    extraction: Extraction,
    state: ConversationState,
    board: ScoreBoard,
    decision: PolicyDecision,
    thresholds: Thresholds,
) -> KnowledgeQuery:
    """Сборка по правилам из docstring модуля."""
    raise NotImplementedError


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
