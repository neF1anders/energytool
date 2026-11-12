"""Dialogue Policy (spec §5.4): что делать на этом ходе. Чистая функция, LLM не участвует.

    decide(state, extraction, board, registry, thresholds, mode) -> PolicyDecision
    after_retrieval(decision, below_floor, mode) -> PolicyDecision

Автомат — docs/diagrams/dialog-state.mmd. Кандидаты: P ≥ 0.25 (максимум 2). Лидер:
P ≥ 0.45 и отрыв ≥ 0.15; смена лидера только при отрыве (гистерезис). В batch-режиме
CONTACT не предлагается, а next_slot остаётся: эталоны в 2/3 случаев заканчиваются
уточняющим вопросом.

below_floor (ответа в базе нет) известен только после ретрива, поэтому переход «вопрос
вне базы → CONTACT» делает отдельная функция after_retrieval.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Extraction, Intent
from energytool.domain.policy import PolicyDecision, Thresholds
from energytool.domain.products import Product
from energytool.domain.scoring import ScoreBoard
from energytool.domain.slots import SlotSpec
from energytool.domain.state import ConversationState, DialogState

Mode = Literal["chat", "batch"]
D = DialogState

NEED_FACTS_INTENTS = frozenset({Intent.QUESTION, Intent.MIXED, Intent.OUT_OF_SCOPE})
CONTACT_REQUIRED = ("contact_name", "phone", "company", "pd_consent")
PHONE = re.compile(r"^\+?[78]?[\s\-()]*(\d[\s\-()]*){10}$")

# разделы, которые подкладываются без поиска (id — как строит BuildKnowledgeIndex)
REQUIREMENTS_SECTION = (
    "00-reference/03-calculation-and-requests.md#какие-сведения-могут-понадобиться/{}"
)
REQUIREMENTS_ANCHORS: dict[Product, str] = {
    Product.OREM: "для-анализа-затрат-и-выхода-на-оптовый-рынок",
    Product.SOLAR_GRID: "для-солнечной-станции",
    Product.SOLAR_HYBRID: "для-солнечной-станции",
    Product.METERING: "для-платформы-учёта",
    Product.LOSSES: "для-поиска-коммерческих-потерь",
    Product.PHOTO: "для-распознавания-показаний",
}
SPECIALIST_SECTION = "00-reference/02-product-scope-and-limits.md#когда-требуется-специалист"


def decide(
    state: ConversationState,
    extraction: Extraction,
    board: ScoreBoard,
    registry: ContractRegistry,
    thresholds: Thresholds,
    mode: Mode = "chat",
) -> PolicyDecision:
    candidates = select_candidates(board, thresholds)
    previous_leader = state.leader_history[-1] if state.leader_history else None
    leader = select_leader(board, thresholds, previous_leader)
    dialog_to, reason = _transition(
        state, extraction, candidates, leader, registry, thresholds, mode
    )
    offer = _offer_contact(state, dialog_to, mode, thresholds)
    pinned: list[str] = []
    if dialog_to in (D.READY, D.NO_CONTACT) and candidates:
        pinned += [REQUIREMENTS_SECTION.format(REQUIREMENTS_ANCHORS[p]) for p in candidates]
    if extraction.intent is Intent.OUT_OF_SCOPE:
        pinned.append(SPECIALIST_SECTION)
    return PolicyDecision(
        dialog_from=state.dialog,
        dialog_to=dialog_to,
        candidates=candidates,
        leader=leader,
        need_facts=extraction.intent in NEED_FACTS_INTENTS,
        next_slot=next_slot(state, dialog_to, candidates, board, registry, thresholds),
        offer_contact=offer,
        pinned_sections=tuple(dict.fromkeys(pinned)),
        reason=reason,
    )


def after_retrieval(
    decision: PolicyDecision, below_floor: bool, mode: Mode = "chat"
) -> PolicyDecision:
    """Ответа в базе нет: в чате переходим к контакту со специалистом, в батче только
    помечаем (ответ всё равно честно скажет, что точной информации нет)."""
    if not below_floor or not decision.need_facts:
        return decision
    if mode == "batch" or decision.dialog_to in (D.CONTACT, D.SUBMITTED):
        return replace(decision, reason=f"{decision.reason}; below_floor")
    return replace(
        decision,
        dialog_to=D.CONTACT,
        offer_contact=True,
        reason=f"{decision.reason}; below_floor → contact",
    )


# --- кандидаты и лидер -----------------------------------------------------------------------


def select_candidates(board: ScoreBoard, thresholds: Thresholds) -> tuple[Product, ...]:
    ranked = [s for s in board.ranked() if s.probability >= thresholds.candidate_probability]
    return tuple(s.product for s in ranked[: thresholds.max_candidates])


def select_leader(
    board: ScoreBoard, thresholds: Thresholds, previous: Product | None
) -> Product | None:
    ranked = board.ranked()
    if not ranked:
        return None
    top = ranked[0]
    second = ranked[1].probability if len(ranked) > 1 else 0.0
    clear = top.probability >= thresholds.leader_probability and (
        top.probability - second >= thresholds.leader_margin
    )
    if previous is not None and previous is not top.product:
        previous_p = board.probability(previous)
        still_strong = previous_p >= thresholds.candidate_probability
        # гистерезис: старый лидер уступает, только если новый оторвался
        if still_strong and top.probability - previous_p < thresholds.leader_margin:
            return previous
    return top.product if clear else None


# --- переходы автомата -----------------------------------------------------------------------


def _transition(
    state: ConversationState,
    extraction: Extraction,
    candidates: tuple[Product, ...],
    leader: Product | None,
    registry: ContractRegistry,
    thresholds: Thresholds,
    mode: Mode,
) -> tuple[DialogState, str]:
    current = state.dialog
    if current is D.SUBMITTED:
        return D.SUBMITTED, "lead already submitted"
    if mode == "chat":
        if current is D.CONTACT and contact_complete(state):
            return D.SUBMITTED, "contact complete"
        if extraction.wants_contact or extraction.intent is Intent.CONTACT:
            return D.CONTACT, "client wants contact"
        if current is D.CONTACT:
            return D.CONTACT, "collecting contact"
        if extraction.intent is Intent.OUT_OF_SCOPE:
            return D.CONTACT, "out of scope → specialist"
        if state.turns_without_progress >= thresholds.max_turns_without_progress:
            return D.CONTACT, f"{state.turns_without_progress} turns without progress"
        if current is D.READY and state.last_cta_turn is not None:
            return D.NO_CONTACT, "contact offered, client continued without it"
        if current is D.NO_CONTACT:
            return D.NO_CONTACT, "client declined contact"
    if leader is None and len(candidates) < 2:
        return D.DISCOVERY, "no leader"
    if is_ready(state, candidates, registry, thresholds):
        return (
            (D.READY, "questionnaire threshold reached")
            if mode == "chat"
            else (D.QUALIFY, "ready (batch: no contact offer)")
        )
    return D.QUALIFY, f"leader={leader}" if leader else "two candidates"


def is_ready(
    state: ConversationState,
    candidates: Sequence[Product],
    registry: ContractRegistry,
    thresholds: Thresholds,
) -> bool:
    if not candidates:
        return False
    for product in candidates:
        if not all(state.is_closed(s.id) for s in registry.gating(product)):
            return False
        required = registry.required(product)
        if required:
            closed = sum(state.is_closed(s.id) for s in required)
            if closed / len(required) < thresholds.ready_completeness:
                return False
    return True


def contact_complete(state: ConversationState) -> bool:
    phone = state.value("phone")
    return (
        all(state.value(slot) not in (None, "") for slot in CONTACT_REQUIRED)
        and state.value("pd_consent") is True
        and isinstance(phone, str)
        and bool(PHONE.match(phone.strip()))
    )


def _offer_contact(
    state: ConversationState, dialog_to: DialogState, mode: Mode, thresholds: Thresholds
) -> bool:
    if mode == "batch":
        return False
    if dialog_to is D.CONTACT:
        return True
    if dialog_to is D.READY:
        return state.last_cta_turn is None
    if dialog_to is D.NO_CONTACT and state.last_cta_turn is not None:
        return state.turn - state.last_cta_turn >= thresholds.cta_cooldown_turns
    return False


# --- следующий вопрос ------------------------------------------------------------------------


def next_slot(
    state: ConversationState,
    dialog: DialogState,
    candidates: Sequence[Product],
    board: ScoreBoard,
    registry: ContractRegistry,
    thresholds: Thresholds,
) -> str | None:
    if dialog is D.SUBMITTED:
        return None
    if dialog is D.CONTACT:
        missing = [s for s in CONTACT_REQUIRED if state.value(s) in (None, "")]
        if state.value("pd_consent") is not True and "pd_consent" not in missing:
            missing.append("pd_consent")
        return missing[0] if missing else None
    pool = (
        _discovery_pool(registry)
        if dialog in (D.OPEN, D.DISCOVERY)
        else registry.active(candidates)
    )
    scored = [
        (value, spec.id)
        for spec in pool
        if (value := slot_value(spec, state, candidates, board, thresholds)) > float("-inf")
    ]
    if not scored:
        return None
    return max(scored, key=lambda pair: pair[0])[1]


def slot_value(
    spec: SlotSpec,
    state: ConversationState,
    candidates: Sequence[Product],
    board: ScoreBoard,
    thresholds: Thresholds,
) -> float:
    """value(slot) = Σ P(p)·weight(slot, p) + бонусы; −∞ — закрыто, спрошено дважды, контакт."""
    if spec.contact or state.is_closed(spec.id):
        return float("-inf")
    if state.asked.get(spec.id, 0) >= thresholds.max_asks_per_slot:
        return float("-inf")
    pool = list(candidates) or [s.product for s in board.ranked()[:2]]
    value = sum(board.probability(p) * spec.weight(p) for p in pool)
    if spec.common and not spec.weights:
        value += 0.5  # общий блок без весов (тип организации, задача) — база для скоринга
    if any(p in spec.gating_for for p in candidates):
        value += thresholds.gating_bonus
    if len(candidates) == 2:
        first, second = candidates
        if (spec.weight(first) > 0) != (spec.weight(second) > 0):
            value += thresholds.discriminating_bonus
    return value


def _discovery_pool(registry: ContractRegistry) -> tuple[SlotSpec, ...]:
    """DISCOVERY: общий блок + поля, важные для ≥ 2 продуктов (различающие)."""
    return tuple(
        s
        for s in registry.slots
        if not s.contact and (s.common or sum(1 for w in s.weights.values() if w > 0) >= 2)
    )
