from energytool.domain.contract import ContractRegistry
from energytool.domain.dialog_policy import (
    REQUIREMENTS_SECTION,
    SPECIALIST_SECTION,
    after_retrieval,
    contact_complete,
    decide,
    is_ready,
    next_slot,
    select_leader,
)
from energytool.domain.extraction import Extraction, Intent
from energytool.domain.policy import PolicyDecision, Thresholds
from energytool.domain.products import ALL_PRODUCTS, Product
from energytool.domain.scoring import ProductScore, ScoreBoard
from energytool.domain.slots import SlotScalar
from energytool.domain.state import DialogState
from tests.domain.helpers import state_from

P, D = Product, DialogState
REG, TH = ContractRegistry(), Thresholds()
INFO = Extraction(Intent.INFO)


def board(**probabilities: float) -> ScoreBoard:
    scores = tuple(
        ProductScore(
            p, 0.0, probabilities.get(p.value, 0.0), probabilities.get(p.value, 0.0) == 0.0
        )
        for p in ALL_PRODUCTS
    )
    return ScoreBoard(turn=1, scores=scores)


LEADER = board(orem=0.7, metering=0.2, solar_grid=0.1)
TWO = board(orem=0.4, metering=0.35, solar_grid=0.25)
NONE = board(orem=0.2, metering=0.2, solar_grid=0.2, photo=0.2, losses=0.2)
OREM_FULL: dict[str, SlotScalar] = {
    "max_power_kw": 1100.0,
    "monthly_consumption_kwh": 1e6,
    "current_supplier": "сбыт",
    "region": "Тула",
}


def go(
    state_kw: dict[str, SlotScalar] | None = None,
    *,
    b: ScoreBoard = LEADER,
    ex: Extraction = INFO,
    dialog: DialogState = D.DISCOVERY,
    mode: str = "chat",
    **state_extra: object,
) -> PolicyDecision:
    state = state_from(state_kw or {}, dialog=dialog)
    for key, value in state_extra.items():
        setattr(state, key, value)
    return decide(state, ex, b, REG, TH, mode)  # type: ignore[arg-type]


def test_discovery_without_leader() -> None:
    decision = go(b=NONE)
    assert decision.dialog_to is D.DISCOVERY
    assert decision.leader is None


def test_discovery_to_qualify_by_leader_or_two_candidates() -> None:
    assert go(b=LEADER).dialog_to is D.QUALIFY
    two = go(b=TWO)
    assert two.dialog_to is D.QUALIFY
    assert two.candidates == (P.OREM, P.METERING)


def test_qualify_to_discovery_when_leader_lost() -> None:
    assert go(b=NONE, dialog=D.QUALIFY).dialog_to is D.DISCOVERY


def test_leader_hysteresis() -> None:
    close = board(orem=0.48, metering=0.40)
    assert select_leader(close, TH, previous=P.METERING) is P.METERING
    far = board(orem=0.7, metering=0.3)
    assert select_leader(far, TH, previous=P.METERING) is P.OREM


def test_qualify_to_ready_offers_contact_once_and_pins_requirements() -> None:
    decision = go(OREM_FULL, dialog=D.QUALIFY)
    assert decision.dialog_to is D.READY
    assert decision.offer_contact is True
    assert decision.pinned_sections == (
        REQUIREMENTS_SECTION.format("для-анализа-затрат-и-выхода-на-оптовый-рынок"),
    )


def test_gating_slot_blocks_ready() -> None:
    without_power = {k: v for k, v in OREM_FULL.items() if k != "max_power_kw"}
    assert not is_ready(state_from(without_power), [P.OREM], REG, TH)
    assert is_ready(state_from({**without_power, "max_power_kw": None}), [P.OREM], REG, TH)


def test_ready_then_no_contact_and_cta_cooldown() -> None:
    declined = go(OREM_FULL, dialog=D.READY, last_cta_turn=1, turn=2)
    assert declined.dialog_to is D.NO_CONTACT
    assert declined.offer_contact is False
    again = go(OREM_FULL, dialog=D.NO_CONTACT, last_cta_turn=1, turn=4)
    assert again.offer_contact is True


def test_any_state_to_contact() -> None:
    assert go(ex=Extraction(Intent.INFO, wants_contact=True)).dialog_to is D.CONTACT
    assert go(ex=Extraction(Intent.CONTACT)).dialog_to is D.CONTACT
    stuck = go(turns_without_progress=8)
    assert stuck.dialog_to is D.CONTACT
    out = go(ex=Extraction(Intent.OUT_OF_SCOPE))
    assert out.dialog_to is D.CONTACT
    assert SPECIALIST_SECTION in out.pinned_sections


def test_contact_to_submitted_requires_valid_phone_and_consent() -> None:
    contact = {"contact_name": "Иван", "company": "Хлебозавод", "phone": "+7 912 345-67-89"}
    assert go({**contact, "pd_consent": True}, dialog=D.CONTACT).dialog_to is D.SUBMITTED
    no_consent = go({**contact, "pd_consent": False}, dialog=D.CONTACT)
    assert no_consent.dialog_to is D.CONTACT
    assert no_consent.next_slot == "pd_consent"
    assert not contact_complete(state_from({**contact, "phone": "123", "pd_consent": True}))
    assert go({**contact, "pd_consent": True}, dialog=D.SUBMITTED).dialog_to is D.SUBMITTED


def test_contact_asks_missing_contact_fields_in_order() -> None:
    assert go(dialog=D.CONTACT).next_slot == "contact_name"
    assert go({"contact_name": "Иван"}, dialog=D.CONTACT).next_slot == "phone"


def test_batch_never_offers_contact_but_keeps_question() -> None:
    decision = go(
        OREM_FULL,
        dialog=D.QUALIFY,
        mode="batch",
        ex=Extraction(Intent.QUESTION, wants_contact=True),
    )
    assert decision.dialog_to is D.QUALIFY
    assert decision.offer_contact is False
    assert decision.next_slot is not None
    assert decision.need_facts is True


def test_after_retrieval_below_floor() -> None:
    decision = go(ex=Extraction(Intent.QUESTION))
    assert after_retrieval(decision, below_floor=True).dialog_to is D.CONTACT
    assert after_retrieval(decision, below_floor=True, mode="batch").dialog_to is decision.dialog_to
    assert after_retrieval(decision, below_floor=False) == decision


def test_next_slot_prefers_gating_field_of_leader() -> None:
    assert go(b=LEADER, dialog=D.QUALIFY).next_slot == "max_power_kw"


def test_next_slot_skips_closed_and_twice_asked() -> None:
    state = state_from({"max_power_kw": 1100.0}, dialog=D.QUALIFY)
    state.asked["monthly_consumption_kwh"] = 2
    slot = next_slot(state, D.QUALIFY, (P.OREM,), LEADER, REG, TH)
    assert slot not in {"max_power_kw", "monthly_consumption_kwh"}


def test_discovery_asks_common_or_shared_fields() -> None:
    slot = go(b=NONE).next_slot
    spec = REG.get(slot or "")
    assert spec.common or sum(1 for w in spec.weights.values() if w > 0) >= 2


def test_full_contacts_in_one_message_submit_from_any_state() -> None:
    contact: dict[str, SlotScalar] = {
        "contact_name": "Иван",
        "company": "Х",
        "phone": "89123456789",
        "pd_consent": True,
    }
    assert go(contact, dialog=D.READY).dialog_to is D.SUBMITTED
