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

from energytool.domain.extraction import Extraction
from energytool.domain.knowledge import KnowledgeQuery
from energytool.domain.policy import PolicyDecision, Thresholds
from energytool.domain.scoring import ScoreBoard
from energytool.domain.state import ConversationState


def build_query(
    extraction: Extraction,
    state: ConversationState,
    board: ScoreBoard,
    decision: PolicyDecision,
    thresholds: Thresholds,
) -> KnowledgeQuery:
    """Сборка по правилам из docstring модуля."""
    raise NotImplementedError
