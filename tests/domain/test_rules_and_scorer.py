import pytest

from energytool.domain.products import Product
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scorer import score
from energytool.domain.scoring import EXCLUDE, ScoringParams
from energytool.domain.slots import SlotScalar
from energytool.domain.state import SoftSignal
from tests.domain.helpers import state_from, state_with

P = Product
RULES_BY_ID = {r.id: r for r in RULES}

# (правило, срабатывает на, не срабатывает на)
CASES: list[tuple[str, dict[str, SlotScalar], dict[str, SlotScalar]]] = [
    ("OREM_GATE", {"max_power_kw": 600.0}, {"max_power_kw": 750.0}),
    ("OREM_POWER", {"max_power_kw": 1100.0}, {"max_power_kw": 749.0}),
    (
        "OREM_VOLUME",
        {"monthly_consumption_kwh": 1_200_000.0},
        {"monthly_consumption_kwh": 900_000.0},
    ),
    (
        "OREM_BORDER",
        {"monthly_consumption_kwh": 900_000.0},
        {"monthly_consumption_kwh": 1_000_000.0},
    ),
    ("OREM_ALREADY", {"already_wholesale": True}, {"already_wholesale": False}),
    ("OREM_ALREADY_PLATFORM", {"already_wholesale": True}, {}),
    ("OREM_SECTOR", {"work_schedule": "три смены"}, {"work_schedule": "днём"}),
    ("OREM_RETAIL", {"segment": "retail_chain"}, {"segment": "industry"}),
    ("PAIN_BILLS", {"goal": "reduce_bill"}, {"goal": "metering"}),
    ("PAIN_BILLS_SOLAR", {"goal": "reduce_bill"}, {}),
    ("GOAL_METERING", {"goal": "metering"}, {"goal": "reduce_bill"}),
    ("GOAL_LOSSES", {"goal": "loss_detection"}, {}),
    ("SOLAR_SITE", {"site_kind": "roof"}, {}),
    ("SOLAR_SITE_HYBRID", {"site_kind": "ground"}, {}),
    ("SOLAR_DAY", {"daytime_share": "high"}, {"daytime_share": "low"}),
    ("SOLAR_DAY_WAREHOUSE", {"segment": "warehouse"}, {"segment": "industry"}),
    ("SOLAR_NIGHT", {"daytime_share": "low"}, {"daytime_share": "medium"}),
    ("SOLAR_NIGHT_HYBRID", {"daytime_share": "low"}, {}),
    (
        "SOLAR_SLATE",
        {"roof_material": "slate", "site_kind": "roof", "roof_age_years": 12},
        {"roof_material": "slate", "site_kind": "both", "roof_age_years": 12},
    ),
    (
        "SOLAR_SLATE_HYBRID",
        {"roof_material": "slate", "site_kind": "roof", "roof_age_years": 5},
        {"roof_material": "slate", "site_kind": "roof", "roof_age_years": 3},
    ),
    (
        "SOLAR_SLATE_UNKNOWN",
        {"roof_material": "slate"},
        {"roof_material": "slate", "roof_age_years": 2},
    ),
    ("HYBRID_OUTAGE", {"outages": "Отключают раз в месяц"}, {"outages": "нет, не бывает"}),
    ("PLATFORM_SPLIT", {"metering_scope": "by_workshop"}, {"metering_scope": "general"}),
    ("PLATFORM_MANUAL", {"collection_method": "техник обходит"}, {"collection_method": "АСКУЭ"}),
    ("HOUSING_LOAD", {"segment": "housing"}, {}),
    ("LOSSES_GATE", {"segment": "sales_company"}, {"segment": "grid_company"}),
    ("LOSSES_GRID", {"segment": "grid_company"}, {"segment": "industry"}),
    ("PHOTO_ORG", {"segment": "resource_supply"}, {"segment": "housing"}),
    ("PHOTO_HOUSING", {"segment": "housing"}, {"segment": "resource_supply"}),
    ("PHOTO_MANUAL", {"meters_connectivity": "without"}, {"meters_connectivity": "with_comm"}),
]


def test_every_rule_has_a_case() -> None:
    assert {c[0] for c in CASES} == set(RULES_BY_ID)


@pytest.mark.parametrize(("rule_id", "fires", "silent"), CASES, ids=[c[0] for c in CASES])
def test_rule_conditions(
    rule_id: str, fires: dict[str, SlotScalar], silent: dict[str, SlotScalar]
) -> None:
    rule = RULES_BY_ID[rule_id]
    assert rule.when(state_from(fires)) is True
    assert rule.when(state_from(silent)) is False


def test_rule_texts_render_slot_values() -> None:
    board = score(state_with(max_power_kw=1100.0), RULES, PRIORS, ScoringParams())
    (hit,) = [h for h in board.trace if h.rule_id == "OREM_POWER"]
    assert hit.text.startswith("мощность 1 100 кВт")
    assert hit.kb_source.startswith("20-products/21-orem")


def test_scorer_excludes_and_normalizes() -> None:
    board = score(
        state_with(segment="industry", max_power_kw=600.0), RULES, PRIORS, ScoringParams()
    )
    assert board.probability(P.OREM) == 0.0
    assert {s.product for s in board.excluded()} == {P.OREM, P.LOSSES}
    assert abs(sum(s.probability for s in board.scores) - 1.0) < 1e-3


def test_correction_recomputes_from_scratch() -> None:
    params = ScoringParams()
    big = score(state_with(segment="industry", max_power_kw=1100.0), RULES, PRIORS, params)
    small = score(state_with(segment="industry", max_power_kw=600.0), RULES, PRIORS, params)
    assert big.ranked()[0].product is P.OREM
    assert P.OREM in {s.product for s in small.excluded()}


def test_soft_signals_decay() -> None:
    state = state_from({}, turn=5)
    fresh = state_from({}, turn=5)
    state.soft_signals.append(SoftSignal(P.PHOTO, 1.0, turn=0, source="mention"))
    fresh.soft_signals.append(SoftSignal(P.PHOTO, 1.0, turn=5, source="mention"))
    params = ScoringParams()
    old_p = score(state, RULES, PRIORS, params).probability(P.PHOTO)
    new_p = score(fresh, RULES, PRIORS, params).probability(P.PHOTO)
    assert new_p > old_p > 1 / 6


def test_flags_do_not_change_score() -> None:
    params = ScoringParams()
    with_flag = score(state_with(monthly_consumption_kwh=10.0), RULES, PRIORS, params)
    without = score(state_with(), RULES, PRIORS, params)
    assert with_flag.probability(P.OREM) == without.probability(P.OREM)
    assert any(h.flag for h in with_flag.trace)


def test_all_excluded_products_have_exclude_weight() -> None:
    assert all(r.weight == EXCLUDE or r.weight > -10 for r in RULES)
