"""Use case AnswerQuestion: один тест-кейс этапа 1 (stateless, spec §6.3)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from enum import StrEnum

from energytool.application.guardrails import guard_origin, guard_query
from energytool.application.knowledge_request import query_from_history
from energytool.application.ports import (
    ExternalServiceError,
    FactExtractor,
    QuestionCondenser,
    ReplyWriter,
    Telemetry,
)
from energytool.application.qualification import Qualifier, Retriever
from energytool.application.trace import EvidenceRef, TurnTrace, collecting_llm_calls
from energytool.application.transcript import last_user_message, parse_transcript
from energytool.domain.contract import ContractRegistry
from energytool.domain.knowledge import (
    BudgetSnippet,
    Evidence,
    KnowledgeQuery,
    KnowledgeResult,
    TextSnippet,
)
from energytool.domain.reply import Reply, ReplyPlan
from energytool.domain.state import ConversationState, Message, Role

logger = logging.getLogger(__name__)


class AnswerMode(StrEnum):
    FULL = "full"
    FAST = "fast"
    NO_LLM = "no_llm"


@dataclass(frozen=True, slots=True)
class AnswerQuestion:
    retrieve: Retriever
    writer: ReplyWriter
    telemetry: Telemetry
    budget: BudgetSnippet = BudgetSnippet()
    extractor: FactExtractor | None = None
    qualifier: Qualifier | None = None
    registry: ContractRegistry = ContractRegistry()
    ask_in_batch: bool = True  # добавлять вопрос анкеты в ответ (проверяется на eval)
    condenser: QuestionCondenser | None = None  # вопрос для поиска из истории диалога
    with_guardrails: bool = True  # подкладывать разделы-ограничения базы (guardrails.py)
    # подтверждения из трассы правил в ответе FULL: в батче модель пересказывает их
    # служебной фразой («Подтверждаю, что вам нужен…»), эталоны так не пишут
    acknowledge_in_batch: bool = False
    # FULL только для диалогов: одиночный вопрос извлекателю нечего сжимать, а наивный
    # путь на них проверен платформой и вдвое быстрее
    full_for_dialogs_only: bool = False

    def __call__(self, case_id: str, cell: str, mode: AnswerMode = AnswerMode.FULL) -> Reply:
        started = time.perf_counter()
        trace = TurnTrace(session_id=case_id, turn=0, mode="batch")
        with collecting_llm_calls() as llm_calls:
            reply = self._answer(cell, mode, trace)
        trace.llm_calls.extend(llm_calls)
        trace.timings_ms["total"] = _ms(started)
        trace.reply_words = len(reply.text.split())
        if not reply.used_llm:
            trace.degraded.append("template_reply")
        self.telemetry.record(trace)
        return reply

    def _answer(self, cell: str, mode: AnswerMode, trace: TurnTrace) -> Reply:
        history = parse_transcript(cell)
        trace.turn = sum(1 for m in history if m.role is Role.USER)
        question = last_user_message(history) or cell.strip()
        if self._takes_full_path(mode, trace.turn):
            plan = self._full_plan(history, question, trace)
            if plan is not None:
                return self._write(plan, history, trace)
        result = self._naive_retrieve(self._naive_query(history, mode), trace)
        plan = ReplyPlan(
            answer_question=question,
            evidence=(*result.evidence, *self._guard_evidence(result, trace)),
            ask=None,
            offer_contact=False,
            weak_evidence=result.below_floor,
            template_only=mode is AnswerMode.NO_LLM,
        )
        return self._write(plan, history, trace)

    def _takes_full_path(self, mode: AnswerMode, user_turns: int) -> bool:
        if mode is not AnswerMode.FULL or self.extractor is None or self.qualifier is None:
            return False
        return user_turns > 1 or not self.full_for_dialogs_only

    def _full_plan(
        self, history: list[Message], question: str, trace: TurnTrace
    ) -> ReplyPlan | None:
        assert self.extractor is not None and self.qualifier is not None
        state = ConversationState(session_id=trace.session_id, turn=trace.turn, history=history)
        started = time.perf_counter()
        try:
            extraction = self.extractor.extract(history, state, self.registry.slots)
        except ExternalServiceError as exc:
            logger.error("extractor failed, naive path: %s", exc)
            trace.degraded.append("extractor_failed")
            return None
        finally:
            trace.timings_ms["extract"] = _ms(started)
        user_text = " ".join(m.text for m in history if m.role is Role.USER)
        step = self.qualifier.step(state, extraction, user_text, "batch", trace)
        # FULL получает то же, что наивный путь: разделы-ограничения, подсказки для
        # вопроса и признак «в базе ответа нет»; иначе выигрыш извлекателя на диалогах
        # съедается потерями на одиночных вопросах
        plan = replace(
            step.plan,
            answer_question=question,
            evidence=(*step.plan.evidence, *self._guard_evidence(step.knowledge, trace)),
            weak_evidence=step.knowledge.below_floor,
            acknowledge=step.plan.acknowledge if self.acknowledge_in_batch else (),
        )
        return plan if self.ask_in_batch else replace(plan, ask=None)

    def _write(self, plan: ReplyPlan, history: list[Message], trace: TurnTrace) -> Reply:
        started = time.perf_counter()
        reply = self.writer.write(plan, history)
        trace.timings_ms["respond"] = _ms(started)
        return reply

    def _naive_query(self, history: list[Message], mode: AnswerMode) -> KnowledgeQuery:
        """Одиночный вопрос ищется как есть; для диалога основной текст — вопрос,
        собранный из истории, последняя реплика остаётся вторым текстом."""
        users = [m.text for m in history if m.role is Role.USER]
        condense = self.condenser is not None and len(users) > 1 and mode is not AnswerMode.NO_LLM
        standalone = self.condenser.condense(history) if self.condenser and condense else ""
        if not standalone:
            return query_from_history(history, self.budget)
        return (
            TextSnippet(standalone, weight=1.0),
            TextSnippet(users[-1], weight=0.5),
            self.budget,
        )

    def _guard_evidence(self, found: KnowledgeResult, trace: TurnTrace) -> tuple[Evidence, ...]:
        """Разделы-ограничения, которых ещё нет среди найденных. Модели не вызываются."""
        if not self.with_guardrails:
            return ()
        try:
            guards = self.retrieve(guard_query(found.scope_mass)).evidence
        except ExternalServiceError as exc:
            logger.error("guardrails lookup failed: %s", exc)
            return ()
        known = {e.chunk.id for e in found.evidence}
        fresh = tuple(
            replace(e, origin=guard_origin(e.chunk.id)) for e in guards if e.chunk.id not in known
        )
        trace.evidence.extend(EvidenceRef(e.chunk.id, e.origin, 1.0) for e in fresh)
        return fresh

    def _naive_retrieve(self, query: KnowledgeQuery, trace: TurnTrace) -> KnowledgeResult:
        started = time.perf_counter()
        try:
            result = self.retrieve(query)
        except ExternalServiceError as exc:
            logger.error("retrieval failed, answering without evidence: %s", exc)
            trace.degraded.append("retrieval_failed")
            result = KnowledgeResult(evidence=())
        trace.timings_ms["retrieve"] = _ms(started)
        trace.need_facts = True
        trace.below_floor = result.below_floor
        trace.evidence = [
            EvidenceRef(e.chunk.id, e.origin, round(e.score, 4)) for e in result.evidence
        ]
        notes = result.trace.get("notes", [])
        if isinstance(notes, list):
            trace.degraded.extend(str(n) for n in notes if str(n).endswith("_failed"))
            trace.fallback_used = any(str(n).startswith("fallback_added") for n in notes)
        return result


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
