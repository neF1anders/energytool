"""Общий шаг квалификации для веба и батча (шаги ②–⑤ spec §6.2):

    reduce → score → decide → build_query → retrieve → after_retrieval → ReplyPlan

Вход — состояние с уже добавленной репликой клиента и Extraction; выход — новое
состояние, доска, решение, найденные знания и план ответа. IO только через
Retriever. Каждый шаг замеряется и пишется в TurnTrace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TypeVar

from energytool.application.knowledge_request import build_query
from energytool.application.ports import ExternalServiceError
from energytool.application.trace import EvidenceRef, RuleHitRef, TurnTrace
from energytool.domain.contract import ContractRegistry
from energytool.domain.dialog_policy import Mode, after_retrieval, decide
from energytool.domain.extraction import Extraction
from energytool.domain.knowledge import BudgetSnippet, KnowledgeQuery, KnowledgeResult
from energytool.domain.policy import PolicyDecision, Thresholds
from energytool.domain.products import Product, Segment
from energytool.domain.reducer import Reduction, apply
from energytool.domain.reply import AskPlan, ReplyPlan
from energytool.domain.scorer import score
from energytool.domain.scoring import Rule, ScoreBoard, ScoringParams
from energytool.domain.state import ConversationState, DialogState, Role

logger = logging.getLogger(__name__)

Retriever = Callable[[KnowledgeQuery], KnowledgeResult]

MAX_ACKNOWLEDGE = 2
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class StepResult:
    state: ConversationState
    board: ScoreBoard
    decision: PolicyDecision
    knowledge: KnowledgeResult
    plan: ReplyPlan


@dataclass(frozen=True, slots=True)
class Qualifier:
    registry: ContractRegistry
    rules: Sequence[Rule]
    priors: Mapping[Segment, Mapping[Product, float]]
    params: ScoringParams
    thresholds: Thresholds
    retrieve: Retriever
    budget: BudgetSnippet
    history_context: bool = False  # прошлые реплики клиента в запросе поиска
    rerank_question: bool = False  # исходный вопрос клиента — контекст реранкера
    rewrite_only: bool = False  # одиночный вопрос ищется только по переформулировке

    def step(
        self,
        state: ConversationState,
        extraction: Extraction,
        turn_text: str,
        mode: Mode,
        trace: TurnTrace,
        template_only: bool = False,
    ) -> StepResult:
        board_before = self._timed(trace, "score_before", lambda: self._score(state))
        reduction = self._timed(
            trace, "reduce", lambda: apply(state, extraction, self.registry, turn_text)
        )
        new_state = reduction.state
        board = self._timed(trace, "score", lambda: self._score(new_state))
        decision = self._timed(
            trace,
            "policy",
            lambda: decide(new_state, extraction, board, self.registry, self.thresholds, mode),
        )
        if mode == "batch":
            # в батче ячейка всегда содержит вопрос, на который нужно ответить по базе
            decision = replace(decision, need_facts=True)
        knowledge = self._knowledge(extraction, new_state, decision, mode, trace)
        decision = after_retrieval(decision, knowledge.below_floor, mode)
        plan = self._plan(
            extraction, new_state, board_before, board, decision, knowledge, template_only
        )
        new_state = self._advance(new_state, decision)
        _fill_trace(trace, extraction, reduction, board_before, board, decision, knowledge)
        return StepResult(new_state, board, decision, knowledge, plan)

    # --- шаги ------------------------------------------------------------------------------

    def _score(self, state: ConversationState) -> ScoreBoard:
        return score(state, self.rules, self.priors, self.params)

    def _knowledge(
        self,
        extraction: Extraction,
        state: ConversationState,
        decision: PolicyDecision,
        mode: Mode,
        trace: TurnTrace,
    ) -> KnowledgeResult:
        if not decision.need_facts and not decision.pinned_sections:
            return KnowledgeResult(evidence=())
        floor = self.thresholds.rerank_floor
        budget = self.budget if floor is None else replace(self.budget, min_rerank_score=floor)
        query = build_query(
            extraction,
            state,
            decision,
            budget,
            restrict_scope=mode == "chat",
            history_context=self.history_context,
            question_context=self.rerank_question,
            rewrite_only=self.rewrite_only,
        )
        started = time.perf_counter()
        try:
            result = self.retrieve(query)
        except ExternalServiceError as exc:
            logger.error("retrieval failed: %s", exc)
            trace.degraded.append("retrieval_failed")
            result = KnowledgeResult(evidence=())
        trace.timings_ms["retrieve"] = _ms(started)
        for stage, ms in result.timings_ms.items():
            trace.timings_ms[f"retrieve.{stage}"] = ms
        notes = result.trace.get("notes", [])
        if isinstance(notes, list):
            trace.degraded.extend(str(n) for n in notes if str(n).endswith("_failed"))
            trace.fallback_used = any(str(n).startswith("fallback_added") for n in notes)
        return result

    def _plan(
        self,
        extraction: Extraction,
        state: ConversationState,
        before: ScoreBoard,
        after: ScoreBoard,
        decision: PolicyDecision,
        knowledge: KnowledgeResult,
        template_only: bool,
    ) -> ReplyPlan:
        question = None
        if decision.need_facts:
            last_user = next((m.text for m in reversed(state.history) if m.role is Role.USER), "")
            question = last_user or extraction.question or None
        ask = None
        if decision.next_slot is not None and self.registry.has(decision.next_slot):
            spec = self.registry.get(decision.next_slot)
            ask = AskPlan(slot=spec, ask=spec.ask, why=spec.why)
        old_hits = {h.rule_id for h in before.trace}
        acknowledge = tuple(
            h.text for h in after.trace if h.rule_id not in old_hits and not h.flag
        )[:MAX_ACKNOWLEDGE]
        return ReplyPlan(
            answer_question=question,
            evidence=knowledge.evidence,
            ask=ask,
            offer_contact=decision.offer_contact,
            acknowledge=acknowledge,
            template_only=template_only,
        )

    @staticmethod
    def _advance(state: ConversationState, decision: PolicyDecision) -> ConversationState:
        """Последствия решения для следующего хода: счётчик вопросов, призыв, лидер."""
        asked = dict(state.asked)
        if decision.next_slot is not None:
            asked[decision.next_slot] = asked.get(decision.next_slot, 0) + 1
        last_cta = state.turn if decision.offer_contact else state.last_cta_turn
        if decision.dialog_to is DialogState.READY and decision.offer_contact:
            last_cta = state.turn
        return replace(
            state,
            dialog=decision.dialog_to,
            asked=asked,
            last_cta_turn=last_cta,
            leader_history=[*state.leader_history, decision.leader],
        )

    @staticmethod
    def _timed(trace: TurnTrace, name: str, fn: Callable[[], _T]) -> _T:
        started = time.perf_counter()
        result = fn()
        trace.timings_ms[name] = _ms(started)
        return result


def _fill_trace(
    trace: TurnTrace,
    extraction: Extraction,
    reduction: Reduction,
    before: ScoreBoard,
    after: ScoreBoard,
    decision: PolicyDecision,
    knowledge: KnowledgeResult,
) -> None:
    trace.extraction = {
        "intent": extraction.intent.value,
        "slot_updates": [[u.slot_id, u.value, u.quote] for u in extraction.slot_updates],
        "accepted": list(reduction.accepted),
        "rejected_updates": [[r.slot_id, r.reason] for r in reduction.rejected],
        "declined": list(extraction.declined),
        "mentions": [p.value for p in extraction.mentions],
        "search_query": extraction.search_query,
    }
    trace.dialog = {
        "from": decision.dialog_from.value,
        "to": decision.dialog_to.value,
        "reason": decision.reason,
        "leader": decision.leader.value if decision.leader else "",
        "candidates": ",".join(p.value for p in decision.candidates),
    }
    trace.scores_before = {s.product.value: s.probability for s in before.scores}
    trace.scores_after = {s.product.value: s.probability for s in after.scores}
    trace.rule_hits = [RuleHitRef(h.rule_id, h.weight, h.kb_source) for h in after.trace]
    trace.next_slot = decision.next_slot
    trace.need_facts = decision.need_facts
    trace.below_floor = knowledge.below_floor
    trace.evidence = [
        EvidenceRef(e.chunk.id, e.origin, round(e.score, 4)) for e in knowledge.evidence
    ]


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
