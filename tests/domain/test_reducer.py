import pytest

from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Extraction, Intent, SlotUpdate
from energytool.domain.products import Product
from energytool.domain.reducer import apply, coerce, quote_in_text
from energytool.domain.slots import SlotScalar, SlotStatus
from tests.domain.helpers import state_with

REG = ContractRegistry()


def reduce(text: str, *updates: SlotUpdate, **kw: object):  # type: ignore[no-untyped-def]
    return apply(state_with(), Extraction(Intent.INFO, slot_updates=updates, **kw), REG, text)  # type: ignore[arg-type]


def test_accepts_quoted_value_and_converts_mw() -> None:
    result = reduce(
        "У нас мощность 1,1 МВт", SlotUpdate("max_power_kw", "1,1", "мощность 1,1 МВт", "МВт")
    )
    assert result.state.value("max_power_kw") == 1100.0
    assert result.accepted == ("max_power_kw",)
    assert result.progressed is True
    assert result.state.turns_without_progress == 0


def test_rejects_missing_or_foreign_quote_and_unknown_slot() -> None:
    result = reduce(
        "Мы склад",
        SlotUpdate("segment", "warehouse", ""),
        SlotUpdate("max_power_kw", 900, "мощность 900 кВт"),
        SlotUpdate("nope", "x", "Мы склад"),
    )
    assert [(r.slot_id, r.reason) for r in result.rejected] == [
        ("segment", "no_quote"),
        ("max_power_kw", "quote_mismatch"),
        ("nope", "unknown_slot"),
    ]
    assert result.state.turns_without_progress == 1


def test_fuzzy_quote_tolerates_small_differences() -> None:
    assert quote_in_text("счётчики стоят у арендаторов", "У нас счетчики  стоят у арендаторов!")
    assert not quote_in_text("крыша из шифера", "У нас профнастил")


def test_correction_keeps_previous_value() -> None:
    first = reduce("1,1 МВт", SlotUpdate("max_power_kw", 1.1, "1,1 МВт", "МВт")).state
    first.turn = 2
    second = apply(
        first,
        Extraction(
            Intent.INFO,
            slot_updates=(SlotUpdate("max_power_kw", 600, "не 1,1 МВт, а 600 кВт", "кВт"),),
        ),
        REG,
        "не 1,1 МВт, а 600 кВт",
    )
    value = second.state.slots["max_power_kw"]
    assert value.value == 600.0
    assert value.previous is not None and value.previous.value == 1100.0


def test_declined_and_mentions() -> None:
    result = reduce("не знаю", declined=("region",), mentions=(Product.PHOTO,))
    assert result.state.slots["region"].status is SlotStatus.DECLINED
    assert result.state.soft_signals[0].product is Product.PHOTO


def test_original_state_is_not_mutated() -> None:
    state = state_with()
    apply(
        state,
        Extraction(Intent.INFO, slot_updates=(SlotUpdate("segment", "industry", "завод"),)),
        REG,
        "завод",
    )
    assert state.slots == {}


@pytest.mark.parametrize(
    ("slot_id", "value", "unit", "quote", "expected"),
    [
        ("monthly_consumption_kwh", "1,2 млн", None, "1,2 млн кВт·ч", 1_200_000.0),
        ("monthly_consumption_kwh", 300, "тыс. кВт·ч", "300 тыс", 300_000.0),
        ("monthly_consumption_kwh", 12, "МВт·ч в год", "12 МВт·ч в год", 1000.0),
        ("max_power_kw", "2 МВт", None, "2 МВт", 2000.0),
        ("meters_count", "около 40", None, "около сорока", 40),
        ("already_wholesale", "да", None, "да", True),
        ("segment", "Industry", None, "завод", "industry"),
        ("doc_tp_act", "later", None, "уточню", "later"),
    ],
)
def test_coerce(
    slot_id: str, value: SlotScalar, unit: str | None, quote: str, expected: SlotScalar
) -> None:
    assert coerce(REG.get(slot_id), value, unit, quote) == expected


@pytest.mark.parametrize(
    ("slot_id", "value", "unit", "reason"),
    [
        ("monthly_consumption_kwh", "3,9 млн", "₽", "unit_mismatch"),
        ("segment", "космодром", None, "bad_value"),
        ("max_power_kw", "много", None, "bad_value"),
        ("already_wholesale", "может быть", None, "bad_value"),
    ],
)
def test_coerce_rejects(slot_id: str, value: SlotScalar, unit: str | None, reason: str) -> None:
    result = coerce(REG.get(slot_id), value, unit)
    assert getattr(result, "reason", None) == reason


@pytest.mark.parametrize(
    ("value", "unit", "quote", "expected"),
    [
        (1100, "МВт", "мощность 1,1 МВт", 1100.0),  # модель уже пересчитала
        (1.1, "МВт", "мощность 1,1 МВт", 1100.0),
        ("1,1", None, "у нас 1,1 МВт", 1100.0),
        (600, "кВт", "не 1,1 МВт, а 600 кВт", 600.0),
        (750, None, "около 750", 750.0),
    ],
)
def test_number_follows_client_quote(
    value: SlotScalar, unit: str | None, quote: str, expected: float
) -> None:
    assert coerce(REG.get("max_power_kw"), value, unit, quote) == expected


def test_choice_is_stored_as_plain_string() -> None:
    value = coerce(REG.get("segment"), "industry", None)
    assert type(value) is str
