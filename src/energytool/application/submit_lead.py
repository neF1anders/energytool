"""Use case SubmitLead (spec §6.4): контакты → бриф → LeadSink."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from energytool.application.ports import BriefRenderer, LeadSink, SessionRepo, Telemetry
from energytool.application.qualification import StepResult
from energytool.application.trace import TurnTrace
from energytool.domain.brief import build, contact_from_state
from energytool.domain.contract import ContractRegistry
from energytool.domain.dialog_policy import contact_complete
from energytool.domain.lead import Contact, Lead
from energytool.domain.products import Product, Segment
from energytool.domain.scorer import score
from energytool.domain.scoring import Rule, ScoreBoard, ScoringParams
from energytool.domain.slots import SlotScalar, SlotStatus, SlotValue
from energytool.domain.state import ConversationState, DialogState, Message, Role

logger = logging.getLogger(__name__)

THANKS = (
    "Спасибо! Заявка принята — менеджер Energytool свяжется с вами в рабочее время "
    "и уже будет знать детали вашей задачи."
)


class LeadValidationError(ValueError):
    """Контакты неполные: нет телефона нужного формата или согласия на обработку ПДн."""


class SessionNotFoundError(LookupError):
    """Заявка для несуществующей сессии."""


@dataclass(frozen=True, slots=True)
class LeadReceipt:
    lead_id: str
    reply: str


@dataclass(frozen=True, slots=True)
class SubmitLead:
    registry: ContractRegistry
    rules: Sequence[Rule]
    priors: Mapping[Segment, Mapping[Product, float]]
    params: ScoringParams
    renderer: BriefRenderer
    sink: LeadSink
    sessions: SessionRepo
    telemetry: Telemetry
    now: Callable[[], datetime]

    def from_step(self, step: StepResult) -> str:
        return self._send(step.state, step.board)

    def from_form(self, session_id: str, contact: Contact) -> LeadReceipt:
        state = self.sessions.load(session_id)
        if state is None:
            raise SessionNotFoundError(session_id)
        turn = state.turn + 1
        filled = _with_contact(state, contact, turn)
        if not contact_complete(filled):
            raise LeadValidationError("нужны имя, компания, телефон (10 цифр) и согласие на ПДн")
        board = score(filled, self.rules, self.priors, self.params)
        lead_id = self._send(filled, board)
        final = replace(
            filled,
            dialog=DialogState.SUBMITTED,
            history=[*filled.history, Message(Role.ASSISTANT, THANKS, turn)],
        )
        trace = TurnTrace(session_id=session_id, turn=turn, mode="chat")
        trace.dialog = {
            "from": state.dialog.value,
            "to": DialogState.SUBMITTED.value,
            "reason": "contact form",
        }
        self.sessions.save(final, trace)
        self.telemetry.record(trace)
        return LeadReceipt(lead_id=lead_id, reply=THANKS)

    def _send(self, state: ConversationState, board: ScoreBoard) -> str:
        brief = build(state, board, self.registry, contact_from_state(state), self.now())
        text, payload = self.renderer.render(brief)
        lead = Lead(id=uuid.uuid4().hex[:12], brief=brief, text=text, payload=payload)
        self.sink.send(lead)
        logger.info(
            "lead built",
            extra={
                "data": {
                    "lead_id": lead.id,
                    "session_id": state.session_id,
                    "candidates": [c.product.value for c in brief.candidates],
                    "missing": list(brief.missing),
                }
            },
        )
        return lead.id


def _with_contact(state: ConversationState, contact: Contact, turn: int) -> ConversationState:
    values: dict[str, SlotScalar] = {
        "contact_name": contact.name.strip(),
        "phone": contact.phone.strip(),
        "company": contact.company.strip(),
        "pd_consent": contact.pd_consent,
        "email": (contact.email or "").strip(),
        "position": (contact.position or "").strip(),
    }
    slots = dict(state.slots)
    for slot_id, value in values.items():
        if value == "":
            continue
        slots[slot_id] = SlotValue(slot_id, value, SlotStatus.FILLED, "форма заявки", turn)
    summary = f"Заявка из формы: {contact.name}, {contact.company}"
    return replace(
        state,
        turn=turn,
        slots=slots,
        history=[*state.history, Message(Role.USER, summary, turn)],
    )
