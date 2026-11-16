"""Brief (spec §5.6): бриф менеджеру из состояния, без пересказа LLM."""

from __future__ import annotations

from datetime import datetime

from energytool.domain.contract import ContractRegistry
from energytool.domain.lead import Brief, CandidateSummary, Contact, ExcludedSummary
from energytool.domain.products import Product
from energytool.domain.scoring import EXCLUDE, ScoreBoard
from energytool.domain.slots import DocumentStatus, SlotStatus, SlotType, SlotValue
from energytool.domain.state import ConversationState, Role

BRIEF_CANDIDATES = 3
# пробелы и документы — только по реальным кандидатам (как Thresholds.candidate_probability)
ACTIVE_PROBABILITY = 0.25


def build(
    state: ConversationState,
    board: ScoreBoard,
    registry: ContractRegistry,
    contact: Contact,
    created_at: datetime,
) -> Brief:
    candidates = _candidates(board)
    return Brief(
        session_id=state.session_id,
        created_at=created_at,
        contact=contact,
        pain=_pain(state),
        candidates=candidates,
        excluded=_excluded(board),
        slots=_slots(state, registry),
        missing=_missing(state, registry, _active(candidates)),
        documents_to_prepare=_documents(state, registry, _active(candidates)),
        transcript_turns=sum(1 for m in state.history if m.role is Role.USER),
    )


def contact_from_state(state: ConversationState) -> Contact:
    def text(slot_id: str) -> str | None:
        value = state.value(slot_id)
        return str(value) if value not in (None, "") else None

    return Contact(
        name=text("contact_name") or "",
        phone=text("phone") or "",
        company=text("company") or "",
        pd_consent=state.value("pd_consent") is True,
        email=text("email"),
        position=text("position"),
        inn=text("inn"),
    )


def _pain(state: ConversationState) -> str:
    pain = state.value("pain")
    if isinstance(pain, str) and pain:
        return pain
    first = next((m.text for m in state.history if m.role is Role.USER), "")
    return first[:300]


def _candidates(board: ScoreBoard) -> tuple[CandidateSummary, ...]:
    return tuple(
        CandidateSummary(
            product=s.product,
            probability=s.probability,
            reasons=tuple(h.text for h in s.hits if not h.flag and h.weight > 0),
            flags=tuple(h.text for h in s.hits if h.flag),
        )
        for s in board.ranked()[:BRIEF_CANDIDATES]
    )


def _excluded(board: ScoreBoard) -> tuple[ExcludedSummary, ...]:
    result = []
    for score in board.excluded():
        hit = next(h for h in score.hits if h.weight == EXCLUDE)
        result.append(
            ExcludedSummary(product=score.product, reason=hit.text, kb_source=hit.kb_source)
        )
    return tuple(result)


def _slots(state: ConversationState, registry: ContractRegistry) -> tuple[SlotValue, ...]:
    order = {spec.id: (spec.opros_item or 100, i) for i, spec in enumerate(registry.slots)}
    values = [
        v
        for v in state.slots.values()
        if v.status is not SlotStatus.EMPTY
        and registry.has(v.slot_id)
        and not registry.get(v.slot_id).contact
    ]
    return tuple(sorted(values, key=lambda v: order[v.slot_id]))


def _missing(
    state: ConversationState, registry: ContractRegistry, products: list[Product]
) -> tuple[str, ...]:
    ids = [s.id for p in products for s in registry.required(p) if not state.is_closed(s.id)]
    return tuple(dict.fromkeys(ids))


def _documents(
    state: ConversationState, registry: ContractRegistry, products: list[Product]
) -> tuple[str, ...]:
    docs = [
        spec.title
        for spec in registry.slots
        if spec.type is SlotType.DOCUMENT
        and any(spec.weight(p) > 0 for p in products)
        and state.value(spec.id) != DocumentStatus.HAVE.value
    ]
    return tuple(dict.fromkeys(docs))


def _active(candidates: tuple[CandidateSummary, ...]) -> list[Product]:
    active = [c.product for c in candidates if c.probability >= ACTIVE_PROBABILITY]
    return active or [c.product for c in candidates[:1]]
