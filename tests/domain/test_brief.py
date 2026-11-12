from datetime import UTC, datetime

from energytool.domain.brief import build, contact_from_state
from energytool.domain.contract import ContractRegistry
from energytool.domain.products import Product
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scorer import score
from energytool.domain.scoring import ScoringParams
from energytool.domain.state import Message, Role
from tests.domain.helpers import state_with

REG = ContractRegistry()


def test_bakery_brief() -> None:
    state = state_with(
        segment="industry",
        goal="reduce_bill",
        max_power_kw=1100.0,
        region="Тульская область",
        monthly_consumption_kwh=None,
        doc_tp_act="have",
        contact_name="Иван",
        phone="+79123456789",
        company="Хлебозавод №1",
        pd_consent=True,
    )
    state.history = [Message(Role.USER, "Счета за свет выросли, у нас хлебозавод", 0)]
    board = score(state, RULES, PRIORS, ScoringParams())
    brief = build(state, board, REG, contact_from_state(state), datetime(2026, 11, 12, tzinfo=UTC))

    assert brief.candidates[0].product is Product.OREM
    assert any("1 100 кВт" in r for r in brief.candidates[0].reasons)
    assert [e.product for e in brief.excluded] == [Product.LOSSES]
    assert brief.pain == "Счета за свет выросли, у нас хлебозавод"
    assert brief.contact.company == "Хлебозавод №1" and brief.contact.pd_consent
    assert "current_supplier" in brief.missing
    assert "monthly_consumption_kwh" not in brief.missing  # «не знаю» = закрыто
    assert all(v.slot_id not in {"phone", "contact_name"} for v in brief.slots)
    items = [REG.get(v.slot_id).opros_item or 100 for v in brief.slots]
    assert items == sorted(items)
    assert "Акт техприсоединения" not in " ".join(brief.documents_to_prepare)
    assert brief.documents_to_prepare
    assert brief.transcript_turns == 1
    assert "site_kind" not in brief.missing  # солнце с вероятностью ~2% не даёт пробелов
