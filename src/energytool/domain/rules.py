"""Таблица правил скоринга и priors по сегменту (spec §5.3).

Правило — чистая функция от состояния: срабатывает на ТЕКУЩИХ значениях слотов, поэтому
правка клиента («не 1,1 МВт, а 600 кВт») сразу меняет картину. У каждого правила —
ссылка на раздел базы, откуда взято условие, и текст для брифа.

Веса стартовые: калибруются на eval/dialog (spec §12), изменения коммитятся сюда же
с комментарием.
"""

from __future__ import annotations

from collections.abc import Mapping

from energytool.domain.products import Goal, Product, Segment
from energytool.domain.scoring import EXCLUDE, Rule
from energytool.domain.slots import SlotScalar
from energytool.domain.state import ConversationState

P = Product
S = Segment

OREM_MIN_POWER_KW = 750.0  # 21-orem › Порог доступа определяется присоединённой мощностью
OREM_VOLUME_KWH = 1_000_000.0  # 21-orem › Экономическая целесообразность (ориентир, не фильтр)
SLATE_MAX_AGE_YEARS = 5  # 23-solar › Не подходит, если: шифер старше 5–10 лет

_MANUAL_COLLECTION = ("вручн", "excel", "эксель", "обход", "техник", "фото", "бумаж", "журнал")
_SHIFT_WORK = ("смен", "круглосуточ", "24/7", "непрерывн")
_NO_OUTAGES = ("нет", "не быва", "редко", "никогда")


def _value(state: ConversationState, slot_id: str) -> SlotScalar | None:
    return state.value(slot_id)


def _number(state: ConversationState, slot_id: str) -> float | None:
    value = _value(state, slot_id)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(state: ConversationState, slot_id: str) -> str:
    value = _value(state, slot_id)
    return value.lower() if isinstance(value, str) else ""


def _segment_in(state: ConversationState, *segments: Segment) -> bool:
    return _value(state, "segment") in {s.value for s in segments}


def _goal_is(state: ConversationState, goal: Goal) -> bool:
    return _value(state, "goal") == goal.value


def _power_below(state: ConversationState) -> bool:
    power = _number(state, "max_power_kw")
    return power is not None and power < OREM_MIN_POWER_KW


def _power_at_least(state: ConversationState) -> bool:
    power = _number(state, "max_power_kw")
    return power is not None and power >= OREM_MIN_POWER_KW


def _volume_at_least(state: ConversationState) -> bool:
    volume = _number(state, "monthly_consumption_kwh")
    return volume is not None and volume >= OREM_VOLUME_KWH


def _volume_below(state: ConversationState) -> bool:
    volume = _number(state, "monthly_consumption_kwh")
    return volume is not None and volume < OREM_VOLUME_KWH


def _shift_work(state: ConversationState) -> bool:
    schedule = _text(state, "work_schedule")
    return any(marker in schedule for marker in _SHIFT_WORK)


def _old_slate_roof_only(state: ConversationState) -> bool:
    age = _number(state, "roof_age_years")
    return (
        _value(state, "roof_material") == "slate"
        and _value(state, "site_kind") == "roof"
        and age is not None
        and age >= SLATE_MAX_AGE_YEARS
    )


def _slate_age_unknown(state: ConversationState) -> bool:
    return _value(state, "roof_material") == "slate" and _number(state, "roof_age_years") is None


def _has_outages(state: ConversationState) -> bool:
    outages = _text(state, "outages")
    return bool(outages) and not any(outages.startswith(m) for m in _NO_OUTAGES)


def _manual_collection(state: ConversationState) -> bool:
    method = _text(state, "collection_method")
    return any(m in method for m in _MANUAL_COLLECTION) or (
        _value(state, "meters_connectivity") == "without"
    )


RULES: tuple[Rule, ...] = (
    # --- выход на оптовый рынок ---
    Rule(
        "OREM_GATE",
        P.OREM,
        _power_below,
        EXCLUDE,
        "мощность {max_power_kw} кВт ниже порога оптового рынка 750 кВт",
        "20-products/21-orem › Порог доступа",
    ),
    Rule(
        "OREM_POWER",
        P.OREM,
        _power_at_least,
        2.0,
        "мощность {max_power_kw} кВт не ниже порога оптового рынка 750 кВт",
        "20-products/21-orem › Порог доступа",
    ),
    Rule(
        "OREM_VOLUME",
        P.OREM,
        _volume_at_least,
        1.5,
        "потребление {monthly_consumption_kwh} кВт·ч/мес — от 1 млн, выход обычно оправдан",
        "20-products/21-orem › Экономическая целесообразность",
    ),
    Rule(
        "OREM_BORDER",
        P.OREM,
        _volume_below,
        0.0,
        "пограничный объём: {monthly_consumption_kwh} кВт·ч/мес ниже ориентира 1 млн",
        "20-products/21-orem › Экономическая целесообразность",
        flag=True,
    ),
    Rule(
        "OREM_ALREADY",
        P.OREM,
        lambda s: _value(s, "already_wholesale") is True,
        EXCLUDE,
        "клиент уже на оптовом рынке",
        "20-products/21-orem › Не подходит, если",
    ),
    Rule(
        "OREM_ALREADY_PLATFORM",
        P.METERING,
        lambda s: _value(s, "already_wholesale") is True,
        1.0,
        "уже на опте: нужен качественный учёт",
        "20-products/21-orem › Не подходит, если",
    ),
    Rule(
        "OREM_SECTOR",
        P.OREM,
        lambda s: _segment_in(s, S.INDUSTRY) or _shift_work(s),
        1.0,
        "энергоёмкое производство или многосменный график",
        "20-products/21-orem › Подходит, если",
    ),
    Rule(
        "OREM_RETAIL",
        P.OREM,
        lambda s: _segment_in(s, S.RETAIL_CHAIN, S.OFFICE_SERVICES),
        -2.0,
        "торговля, офис или ИТ без производственной нагрузки",
        "20-products/21-orem › Не подходит, если",
    ),
    # --- цель клиента ---
    Rule(
        "PAIN_BILLS",
        P.OREM,
        lambda s: _goal_is(s, Goal.REDUCE_BILL),
        1.0,
        "цель — снизить платёж за электроэнергию",
        "00-reference/00-product-map",
    ),
    Rule(
        "PAIN_BILLS_SOLAR",
        P.SOLAR_GRID,
        lambda s: _goal_is(s, Goal.REDUCE_BILL),
        0.5,
        "цель — снизить платёж за электроэнергию",
        "00-reference/00-product-map",
    ),
    Rule(
        "GOAL_METERING",
        P.METERING,
        lambda s: _goal_is(s, Goal.METERING),
        1.5,
        "цель — автоматизировать учёт",
        "00-reference/00-product-map",
    ),
    Rule(
        "GOAL_LOSSES",
        P.LOSSES,
        lambda s: _goal_is(s, Goal.LOSS_DETECTION),
        2.0,
        "цель — найти потери",
        "00-reference/00-product-map",
    ),
    # --- солнечные станции ---
    Rule(
        "SOLAR_SITE",
        P.SOLAR_GRID,
        lambda s: _value(s, "site_kind") is not None,
        1.5,
        "есть площадка под станцию: {site_kind}",
        "20-products/23-solar › Размещение",
    ),
    Rule(
        "SOLAR_SITE_HYBRID",
        P.SOLAR_HYBRID,
        lambda s: _value(s, "site_kind") is not None,
        1.0,
        "есть площадка под станцию: {site_kind}",
        "20-products/23-solar › Размещение",
    ),
    Rule(
        "SOLAR_DAY",
        P.SOLAR_GRID,
        lambda s: _value(s, "daytime_share") == "high",
        1.5,
        "основное потребление днём",
        "20-products/23-solar › Лучший профиль клиента",
    ),
    Rule(
        "SOLAR_DAY_WAREHOUSE",
        P.SOLAR_GRID,
        lambda s: _segment_in(s, S.WAREHOUSE),
        1.0,
        "склад: большая кровля и дневной режим",
        "20-products/20-portfolio › Склад, складской комплекс",
    ),
    Rule(
        "SOLAR_NIGHT",
        P.SOLAR_GRID,
        lambda s: _value(s, "daytime_share") == "low",
        EXCLUDE,
        "потребление в основном вечером и ночью — сетевая станция без накопителей не подходит",
        "20-products/23-solar › Не подходит, если",
    ),
    Rule(
        "SOLAR_NIGHT_HYBRID",
        P.SOLAR_HYBRID,
        lambda s: _value(s, "daytime_share") == "low",
        1.0,
        "вечерний профиль: перенос выработки накопителями",
        "20-products/23-solar › Два типа станций",
    ),
    Rule(
        "SOLAR_SLATE",
        P.SOLAR_GRID,
        _old_slate_roof_only,
        EXCLUDE,
        "единственная площадка — шиферная кровля старше 5 лет",
        "20-products/23-solar › Не подходит, если",
    ),
    Rule(
        "SOLAR_SLATE_HYBRID",
        P.SOLAR_HYBRID,
        _old_slate_roof_only,
        EXCLUDE,
        "единственная площадка — шиферная кровля старше 5 лет",
        "20-products/23-solar › Не подходит, если",
    ),
    Rule(
        "SOLAR_SLATE_UNKNOWN",
        P.SOLAR_GRID,
        _slate_age_unknown,
        0.0,
        "шиферная кровля: уточнить возраст",
        "20-products/23-solar › Не подходит, если",
        flag=True,
    ),
    Rule(
        "HYBRID_OUTAGE",
        P.SOLAR_HYBRID,
        lambda s: _has_outages(s) or _goal_is(s, Goal.RELIABILITY),
        2.5,
        "важна надёжность: бывают отключения внешней сети",
        "00-reference/00-product-map",
    ),
    # --- платформа учёта ---
    Rule(
        "PLATFORM_SPLIT",
        P.METERING,
        lambda s: _value(s, "metering_scope") in {"by_workshop", "by_site", "by_tenant"},
        2.5,
        "нужен учёт по цехам, объектам или арендаторам ({metering_scope})",
        "20-products/22-askue › Кому подходит",
    ),
    Rule(
        "PLATFORM_MANUAL",
        P.METERING,
        _manual_collection,
        1.0,
        "показания собирают вручную",
        "20-products/22-askue › Что даёт клиенту",
    ),
    Rule(
        "HOUSING_LOAD",
        P.METERING,
        lambda s: _segment_in(s, S.HOUSING),
        2.0,
        "УК, ТСЖ или СНТ: учёт по абонентам и управление нагрузкой",
        "20-products/20-portfolio › Управляющая компания, ТСЖ, СНТ, товарищество",
    ),
    # --- потери ---
    Rule(
        "LOSSES_GATE",
        P.LOSSES,
        lambda s: _segment_in(s, S.INDUSTRY, S.SALES_COMPANY),
        EXCLUDE,
        "поиск потерь не для промышленных потребителей и энергосбытов",
        "20-products/24-losses › Кому подходит",
    ),
    Rule(
        "LOSSES_GRID",
        P.LOSSES,
        lambda s: _segment_in(s, S.GRID_COMPANY),
        3.0,
        "сетевая компания: потери в сети — её зона ответственности",
        "20-products/24-losses › Кому подходит",
    ),
    # --- фото-показания ---
    Rule(
        "PHOTO_ORG",
        P.PHOTO,
        lambda s: _segment_in(s, S.RESOURCE_SUPPLY, S.GRID_COMPANY, S.HOUSING, S.SALES_COMPANY),
        2.0,
        "организация собирает показания у абонентов",
        "20-products/25-meter-reading › Кому подходит",
    ),
    Rule(
        "PHOTO_MANUAL",
        P.PHOTO,
        _manual_collection,
        0.5,
        "часть приборов не подключена: показания вручную",
        "20-products/20-portfolio › Как продукты связаны между собой",
    ),
)


# Priors: стартовая уверенность по типу компании (20-portfolio › Навигация по типу компании).
# Значения — добавки к баллу до softmax; отсутствующий сегмент = 0 для всех.
PRIORS: Mapping[Segment, Mapping[Product, float]] = {
    S.INDUSTRY: {P.OREM: 1.0, P.METERING: 0.7, P.SOLAR_GRID: 0.5, P.SOLAR_HYBRID: 0.2},
    S.WAREHOUSE: {P.SOLAR_GRID: 1.2, P.METERING: 0.4, P.SOLAR_HYBRID: 0.3},
    S.RETAIL_CHAIN: {P.METERING: 1.2, P.SOLAR_GRID: 0.6, P.SOLAR_HYBRID: 0.2},
    S.BUSINESS_CENTER: {P.METERING: 1.2, P.SOLAR_GRID: 0.6, P.SOLAR_HYBRID: 0.2},
    S.OFFICE_SERVICES: {P.METERING: 0.5, P.SOLAR_GRID: 0.2},
    S.HOUSING: {P.METERING: 1.2, P.PHOTO: 1.0},
    S.GRID_COMPANY: {P.LOSSES: 1.2, P.METERING: 1.0, P.PHOTO: 0.8},
    S.SALES_COMPANY: {P.METERING: 1.0, P.PHOTO: 1.0},
    S.RESOURCE_SUPPLY: {P.PHOTO: 1.2, P.METERING: 1.0},
    S.DEVELOPER: {P.METERING: 0.5, P.SOLAR_GRID: 0.5},
    S.AGRO: {P.SOLAR_GRID: 0.6, P.SOLAR_HYBRID: 0.5, P.METERING: 0.4, P.OREM: 0.3},
}
