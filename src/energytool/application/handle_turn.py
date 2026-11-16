"""Use case HandleTurn: один ход веб-чата (spec §6.2, docs/diagrams/turn-sequence.mmd)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from energytool.application.ports import (
    ExternalServiceError,
    FactExtractor,
    ReplyWriter,
    SessionRepo,
    Telemetry,
)
from energytool.application.qualification import Qualifier, StepResult
from energytool.application.submit_lead import THANKS
from energytool.application.trace import TurnTrace, collecting_llm_calls
from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Extraction, Intent
from energytool.domain.reply import Reply
from energytool.domain.state import ConversationState, DialogState, Message, Role

logger = logging.getLogger(__name__)

# ⑦: вызывается при переходе в SUBMITTED; возвращает id заявки (SubmitLead, M6)
LeadSubmitter = Callable[[StepResult], str | None]


@dataclass(frozen=True, slots=True)
class TurnResponse:
    session_id: str
    reply: Reply
    dialog: DialogState
    trace: TurnTrace
    lead_id: str | None = None


@dataclass(frozen=True, slots=True)
class HandleTurn:
    sessions: SessionRepo
    extractor: FactExtractor
    qualifier: Qualifier
    writer: ReplyWriter
    telemetry: Telemetry
    registry: ContractRegistry = ContractRegistry()
    submit_lead: LeadSubmitter | None = None

    def __call__(self, session_id: str, text: str) -> TurnResponse:
        started = time.perf_counter()
        state = self._open(session_id, text)
        trace = TurnTrace(session_id=session_id, turn=state.turn, mode="chat")
        with collecting_llm_calls() as llm_calls:
            extraction = self._extract(state, text, trace)
            step = self.qualifier.step(state, extraction, text, "chat", trace)
            reply = self._write(step, trace)
            if _just_submitted(step):
                reply = Reply(text=THANKS, used_llm=False, evidence_ids=reply.evidence_ids)
            lead_id = self._submit(step, trace)
        trace.llm_calls.extend(llm_calls)
        final = replace(
            step.state,
            history=[*step.state.history, Message(Role.ASSISTANT, reply.text, state.turn)],
        )
        trace.reply_words = len(reply.text.split())
        if not reply.used_llm and step.plan.answer_question:
            trace.degraded.append("template_reply")
        trace.timings_ms["total"] = round((time.perf_counter() - started) * 1000, 1)
        self.sessions.save(final, trace)
        self.telemetry.record(trace)
        return TurnResponse(session_id, reply, final.dialog, trace, lead_id)

    def _open(self, session_id: str, text: str) -> ConversationState:
        state = self.sessions.load(session_id) or ConversationState(session_id=session_id)
        turn = state.turn + 1
        return replace(
            state,
            turn=turn,
            history=[*state.history, Message(Role.USER, text.strip(), turn)],
            slots=dict(state.slots),
            asked=dict(state.asked),
            soft_signals=list(state.soft_signals),
            leader_history=list(state.leader_history),
        )

    def _extract(self, state: ConversationState, text: str, trace: TurnTrace) -> Extraction:
        started = time.perf_counter()
        try:
            return self.extractor.extract(state.history, state, self.registry.slots)
        except ExternalServiceError as exc:
            logger.error("extractor failed, question-only turn: %s", exc)
            trace.degraded.append("extractor_failed")
            return Extraction(intent=Intent.QUESTION, search_query=text)
        finally:
            trace.timings_ms["extract"] = round((time.perf_counter() - started) * 1000, 1)

    def _write(self, step: StepResult, trace: TurnTrace) -> Reply:
        started = time.perf_counter()
        reply = self.writer.write(step.plan, step.state.history)
        trace.timings_ms["respond"] = round((time.perf_counter() - started) * 1000, 1)
        return reply

    def _submit(self, step: StepResult, trace: TurnTrace) -> str | None:
        if not _just_submitted(step) or self.submit_lead is None:
            return None
        try:
            lead_id = self.submit_lead(step)
        except ExternalServiceError as exc:
            logger.error("lead submission failed: %s", exc)
            trace.degraded.append("lead_failed")
            return None
        logger.info("lead submitted", extra={"data": {"lead_id": lead_id}})
        return lead_id


def _just_submitted(step: StepResult) -> bool:
    return (
        step.decision.dialog_to is DialogState.SUBMITTED
        and step.decision.dialog_from is not DialogState.SUBMITTED
    )
