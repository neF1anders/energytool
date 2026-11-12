"""Use case AnswerQuestion: один тест-кейс этапа 1 (stateless, spec §6.3).

Наивный путь M2: ячейка → история → запрос из истории → RetrieveKnowledge → ReplyWriter.
Extractor, Policy и анкета подключаются в M4; интерфейс use case при этом не меняется.

Режимы бюджета (выбирает batch-раннер по оставшемуся времени):
- FULL     — ретрив с моделями + LLM-ответ;
- NO_LLM   — ретрив + шаблон из лучшего фрагмента (страховка от тайм-аута прогона).

Ни один кейс не возвращает пустой ответ: ошибки внешних сервисов уходят в деградацию,
непредвиденная ошибка — в шаблон с записью в лог.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from energytool.application.knowledge_request import query_from_history
from energytool.application.ports import ExternalServiceError, ReplyWriter, Telemetry
from energytool.application.trace import EvidenceRef, TurnTrace, collecting_llm_calls
from energytool.application.transcript import last_user_message, parse_transcript
from energytool.domain.knowledge import BudgetSnippet, KnowledgeQuery, KnowledgeResult
from energytool.domain.reply import Reply, ReplyPlan

logger = logging.getLogger(__name__)

Retriever = Callable[[KnowledgeQuery], KnowledgeResult]


class AnswerMode(StrEnum):
    FULL = "full"
    NO_LLM = "no_llm"


@dataclass(frozen=True, slots=True)
class AnswerQuestion:
    retrieve: Retriever
    writer: ReplyWriter
    telemetry: Telemetry
    budget: BudgetSnippet = BudgetSnippet()

    def __call__(self, case_id: str, cell: str, mode: AnswerMode = AnswerMode.FULL) -> Reply:
        started = time.perf_counter()
        trace = TurnTrace(session_id=case_id, turn=0, mode="batch")
        with collecting_llm_calls() as llm_calls:
            reply = self._answer(cell, mode, trace)
        trace.llm_calls.extend(llm_calls)
        trace.timings_ms["total"] = round((time.perf_counter() - started) * 1000, 1)
        trace.reply_words = len(reply.text.split())
        if not reply.used_llm:
            trace.degraded.append("template_reply")
        self.telemetry.record(trace)
        return reply

    def _answer(self, cell: str, mode: AnswerMode, trace: TurnTrace) -> Reply:
        history = parse_transcript(cell)
        trace.turn = len(history)
        question = last_user_message(history) or cell.strip()
        result = self._retrieve(query_from_history(history, self.budget), trace)
        plan = ReplyPlan(
            answer_question=question,
            evidence=result.evidence,
            ask=None,
            offer_contact=False,
            template_only=mode is AnswerMode.NO_LLM,
        )
        started = time.perf_counter()
        reply = self.writer.write(plan, history)
        trace.timings_ms["respond"] = round((time.perf_counter() - started) * 1000, 1)
        return reply

    def _retrieve(self, query: KnowledgeQuery, trace: TurnTrace) -> KnowledgeResult:
        started = time.perf_counter()
        try:
            result = self.retrieve(query)
        except ExternalServiceError as exc:
            logger.error("retrieval failed, answering without evidence: %s", exc)
            trace.degraded.append("retrieval_failed")
            result = KnowledgeResult(evidence=())
        trace.timings_ms["retrieve"] = round((time.perf_counter() - started) * 1000, 1)
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
