"""Contract Registry: каталог всех полей анкеты и запросы к нему.

Источники полей:
- «Опросный лист ОРЭМ» от менеджера (16 пунктов) — номер в ``opros_item``;
- база знаний 00-reference/03-calculation-and-requests, «Какие сведения могут понадобиться»;
- звонок пресейлу №1 (менеджер просил типы и марки ПУ для платформы учёта).

Принцип «слушаем всё, спрашиваем по лидерам»: Extractor получает весь каталог,
Dialogue Policy спрашивает только поля активного контракта
(общий блок + блоки продуктов-кандидатов).

Веса и состав обязательных полей — стартовые, калибруются на eval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from energytool.domain.products import Goal, Product, Segment
from energytool.domain.slots import SlotSpec, SlotType

P = Product
_ = frozenset

# --- общий блок -----------------------------------------------------------------------------

COMMON: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="segment", title="Тип организации", type=SlotType.CHOICE,
        choices=tuple(Segment), common=True,
        ask="Расскажите, чем занимается ваша организация?",
        why="от этого зависит, какие решения вам подходят",
        kb_source="20-products/20-portfolio › Навигация по типу компании",
    ),
    SlotSpec(
        id="goal", title="Задача клиента", type=SlotType.CHOICE,
        choices=tuple(Goal), common=True,
        ask="Что для вас важнее: снизить платёж, наладить учёт, найти потери или повысить надёжность?",
        kb_source="00-reference/03-calculation-and-requests › Для первичного понимания задачи",
    ),
    SlotSpec(
        id="pain", title="Боль своими словами", type=SlotType.TEXT, common=True,
        ask="Что вас беспокоит сейчас?",
    ),
    SlotSpec(
        id="region", title="Регион(ы)", type=SlotType.TEXT, common=True, opros_item=2,
        required_for=_(P), weights={p: 0.4 for p in P},
        ask="В каком регионе находится объект?",
        why="от региона зависят тарифы и инсоляция",
    ),
    SlotSpec(
        id="sites_count", title="Число объектов / площадок", type=SlotType.INTEGER, common=True,
        weights={P.OREM: 0.3, P.METERING: 0.5},
        ask="Сколько у вас объектов или площадок?",
    ),
)

# --- контакты (спрашиваем только в состоянии CONTACT) --------------------------------------

CONTACT: tuple[SlotSpec, ...] = (
    SlotSpec(id="contact_name", title="ФИО", type=SlotType.TEXT, contact=True, opros_item=12,
             ask="Как к вам обращаться?"),
    SlotSpec(id="phone", title="Телефон", type=SlotType.TEXT, contact=True, opros_item=12,
             ask="Оставьте телефон, менеджер свяжется с вами."),
    SlotSpec(id="company", title="Компания", type=SlotType.TEXT, contact=True, opros_item=1,
             ask="Как называется компания?"),
    SlotSpec(id="email", title="E-mail", type=SlotType.TEXT, contact=True, opros_item=12,
             ask="Если удобно, оставьте e-mail для расчёта."),
    SlotSpec(id="position", title="Должность", type=SlotType.TEXT, contact=True, opros_item=12,
             ask="Какая у вас должность?"),
    SlotSpec(id="inn", title="ИНН", type=SlotType.TEXT, contact=True, opros_item=1,
             ask="Если готовы, укажите ИНН — это ускорит расчёт."),
    SlotSpec(id="pd_consent", title="Согласие на обработку ПДн", type=SlotType.BOOL, contact=True,
             ask="Подтвердите, пожалуйста, согласие на обработку персональных данных.",
             kb_source="00-reference/03-calculation-and-requests › Согласие на обработку персональных данных"),
)

# --- выход на оптовый рынок -----------------------------------------------------------------

OREM: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="max_power_kw", title="Максимальная мощность по документам ТП", type=SlotType.NUMBER,
        unit="кВт", opros_item=3, weights={P.OREM: 1.0, P.SOLAR_GRID: 0.6, P.SOLAR_HYBRID: 0.6},
        required_for=_({P.OREM}), gating_for=_({P.OREM}),
        ask="Какая у вас максимальная мощность по акту техприсоединения?",
        why="от 750 кВт открывается оптовый рынок",
        kb_source="20-products/21-orem › Порог доступа определяется присоединённой мощностью",
    ),
    SlotSpec(
        id="monthly_consumption_kwh", title="Потребление в месяц", type=SlotType.NUMBER,
        unit="кВт·ч/мес", weights={P.OREM: 0.9, P.SOLAR_GRID: 0.4},
        required_for=_({P.OREM}),
        ask="Сколько примерно потребляете в месяц, в кВт·ч или в рублях по счёту?",
        why="от объёма зависит окупаемость",
        kb_source="20-products/21-orem › Экономическая целесообразность",
    ),
    SlotSpec(
        id="current_supplier", title="Текущий сбыт / гарантирующий поставщик", type=SlotType.TEXT,
        opros_item=5, weights={P.OREM: 0.7}, required_for=_({P.OREM}),
        ask="У кого сейчас покупаете электроэнергию?",
    ),
    SlotSpec(
        id="already_wholesale", title="Уже на оптовом рынке", type=SlotType.BOOL,
        weights={P.OREM: 0.6},
        ask="Вы уже покупаете на оптовом рынке, например через сбытовую компанию?",
        kb_source="20-products/21-orem › Не подходит, если",
    ),
    SlotSpec(
        id="voltage_level", title="Уровень напряжения", type=SlotType.CHOICE,
        choices=("VN", "SN1", "SN2", "NN", "unknown"), weights={P.OREM: 0.5},
        ask="На каком напряжении вы подключены, если знаете?",
        kb_source="10-energy/12-power-thresholds › Уровни напряжения",
    ),
    SlotSpec(
        id="price_category", title="Ценовая категория", type=SlotType.CHOICE, opros_item=15,
        choices=("1", "2", "3", "4", "5", "6", "unknown"), weights={P.OREM: 0.5},
        ask="Какая ценовая категория указана в договоре энергоснабжения?",
    ),
    SlotSpec(
        id="delivery_points", title="Точек поставки / измерения", type=SlotType.INTEGER,
        opros_item=7, weights={P.OREM: 0.5, P.METERING: 0.3},
        ask="Сколько у вас точек поставки?",
    ),
    SlotSpec(
        id="has_subconsumers", title="Есть субабоненты", type=SlotType.BOOL, opros_item=7,
        weights={P.OREM: 0.3},
        ask="Есть ли у вас субабоненты, которые питаются через вашу сеть?",
    ),
    SlotSpec(
        id="sites_connected", title="Площадки связаны электрически (0,4 кВ)", type=SlotType.BOOL,
        weights={P.OREM: 0.3},
        ask="Если площадок несколько, они связаны между собой электрически?",
        why="мощность суммируется только у связанных площадок",
        kb_source="20-products/21-orem › Если у предприятия несколько площадок",
    ),
    SlotSpec(
        id="transmission_contract", title="Отдельный договор на передачу", type=SlotType.BOOL,
        opros_item=6, weights={P.OREM: 0.2},
        ask="Есть ли у вас отдельный договор на услуги по передаче электроэнергии?",
    ),
    SlotSpec(
        id="metering_system", title="Система учёта: сервер, ПО, типы ПУ, каналы связи",
        type=SlotType.TEXT, opros_item=9, weights={P.OREM: 0.5, P.METERING: 0.4},
        ask="Какая у вас система учёта: есть ли сервер, ПО, какие приборы и каналы связи?",
    ),
    SlotSpec(
        id="work_schedule", title="График работы", type=SlotType.TEXT, opros_item=16,
        weights={P.OREM: 0.4, P.SOLAR_GRID: 0.6, P.SOLAR_HYBRID: 0.4},
        ask="Как вы работаете: круглосуточно, в несколько смен, днём?",
    ),
    # документы: отмечаем наличие, из них собирается список «что подготовить»
    SlotSpec(id="doc_tp_act", title="Акт техприсоединения", type=SlotType.DOCUMENT, opros_item=4,
             weights={P.OREM: 0.2}, ask="Акт техприсоединения у вас под рукой?"),
    SlotSpec(id="doc_single_line", title="Однолинейная схема", type=SlotType.DOCUMENT, opros_item=8,
             weights={P.OREM: 0.2}, ask="Есть ли однолинейная схема присоединения?"),
    SlotSpec(id="doc_verification", title="Действующая поверка СИ", type=SlotType.DOCUMENT,
             opros_item=10, weights={P.OREM: 0.1}, ask="Поверка счётчиков и трансформаторов действующая?"),
    SlotSpec(id="doc_admission_acts", title="Акты допуска узлов учёта", type=SlotType.DOCUMENT,
             opros_item=11, weights={P.OREM: 0.1}, ask="Есть ли акты допуска узлов учёта?"),
    SlotSpec(id="doc_monthly_year", title="Потребление за год помесячно", type=SlotType.DOCUMENT,
             opros_item=13, weights={P.OREM: 0.2}, ask="Сможете прислать потребление за год по месяцам?"),
    SlotSpec(id="doc_hourly_profiles", title="Почасовые профили в режимные дни",
             type=SlotType.DOCUMENT, opros_item=14, weights={P.OREM: 0.1},
             ask="Есть ли почасовые профили потребления?"),
)

# --- платформа учёта ------------------------------------------------------------------------

METERING: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="meters_count", title="Количество приборов учёта", type=SlotType.INTEGER,
        weights={P.METERING: 1.0, P.LOSSES: 0.8, P.PHOTO: 0.6},
        required_for=_({P.METERING, P.LOSSES}), gating_for=_({P.METERING}),
        ask="Сколько примерно у вас приборов учёта?",
        kb_source="00-reference/03-calculation-and-requests › Для платформы учёта",
    ),
    SlotSpec(
        id="meter_models", title="Типы и марки ПУ", type=SlotType.TEXT,
        weights={P.METERING: 0.8, P.PHOTO: 0.4}, required_for=_({P.METERING}),
        ask="Какие у вас приборы учёта, хотя бы производители?",
        why="по ним проверим совместимость с платформой",
    ),
    SlotSpec(
        id="meters_connectivity", title="Связь у приборов", type=SlotType.CHOICE,
        choices=("with_comm", "without", "mixed", "unknown"), weights={P.METERING: 0.6, P.PHOTO: 0.5},
        ask="Приборы передают данные сами или показания снимают вручную?",
    ),
    SlotSpec(
        id="sim_operator", title="Оператор SIM в приборах", type=SlotType.TEXT,
        weights={P.METERING: 0.2}, ask="Чьи SIM-карты стоят в счётчиках?",
    ),
    SlotSpec(
        id="collection_method", title="Как собирают показания сейчас", type=SlotType.TEXT,
        weights={P.METERING: 0.7, P.PHOTO: 0.7}, required_for=_({P.METERING}),
        ask="Как вы сейчас собираете показания?",
    ),
    SlotSpec(
        id="metering_scope", title="Разрез учёта", type=SlotType.CHOICE,
        choices=("by_workshop", "by_site", "by_tenant", "by_subscriber", "load_control", "general"),
        weights={P.METERING: 0.8}, required_for=_({P.METERING}),
        ask="Учёт нужен по цехам, по объектам, по арендаторам или по абонентам?",
        kb_source="00-reference/03-calculation-and-requests › Для платформы учёта",
    ),
)

# --- фото-показания -------------------------------------------------------------------------

PHOTO: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="resource_type", title="Тип ресурса", type=SlotType.CHOICE,
        choices=("electricity", "water", "heat", "gas", "mixed"), weights={P.PHOTO: 0.8},
        required_for=_({P.PHOTO}), gating_for=_({P.PHOTO}),
        ask="Показания какого ресурса нужно принимать?",
        kb_source="00-reference/03-calculation-and-requests › Для распознавания показаний",
    ),
    SlotSpec(
        id="photo_channel", title="Как передают фото", type=SlotType.TEXT, weights={P.PHOTO: 0.6},
        required_for=_({P.PHOTO}), ask="Как абоненты сейчас передают показания?",
    ),
    SlotSpec(
        id="photo_volume", title="Объём потока / архива фото", type=SlotType.TEXT,
        weights={P.PHOTO: 0.5}, ask="Сколько показаний в месяц приходит, есть ли архив фото?",
    ),
)

# --- поиск коммерческих потерь --------------------------------------------------------------

LOSSES: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="metering_export", title="Можно выгрузить данные учёта", type=SlotType.BOOL,
        weights={P.LOSSES: 0.8}, required_for=_({P.LOSSES}), gating_for=_({P.LOSSES}),
        ask="Сможете сделать обезличенную выгрузку данных учёта?",
        kb_source="00-reference/03-calculation-and-requests › Для поиска коммерческих потерь",
    ),
    SlotSpec(
        id="data_granularity", title="Детализация данных", type=SlotType.CHOICE,
        choices=("daily_day_night", "hourly", "half_hourly", "unknown"), weights={P.LOSSES: 0.6},
        ask="Данные есть по суткам с разбивкой на день и ночь или почасовые?",
    ),
    SlotSpec(
        id="archive_months", title="Глубина архива, мес.", type=SlotType.INTEGER,
        weights={P.LOSSES: 0.5}, ask="За какой период есть архив данных?",
        why="нужен архив не меньше года",
    ),
)

# --- солнечные станции ----------------------------------------------------------------------

SOLAR: tuple[SlotSpec, ...] = (
    SlotSpec(
        id="site_kind", title="Кровля или площадка", type=SlotType.CHOICE,
        choices=("roof", "ground", "both"), weights={P.SOLAR_GRID: 0.8, P.SOLAR_HYBRID: 0.6},
        required_for=_({P.SOLAR_GRID, P.SOLAR_HYBRID}), gating_for=_({P.SOLAR_GRID}),
        ask="Станцию можно поставить на кровлю или есть свободная площадка?",
        kb_source="20-products/23-solar › Размещение",
    ),
    SlotSpec(
        id="roof_material", title="Материал кровли", type=SlotType.CHOICE,
        choices=("profiled_sheet", "soft_roof", "slate", "concrete", "other"),
        weights={P.SOLAR_GRID: 0.6, P.SOLAR_HYBRID: 0.4},
        ask="Из чего кровля?", why="на старый шифер панели ставить невыгодно",
        kb_source="20-products/23-solar › Не подходит, если",
    ),
    SlotSpec(
        id="roof_age_years", title="Возраст кровли, лет", type=SlotType.INTEGER,
        weights={P.SOLAR_GRID: 0.3}, ask="Сколько лет кровле?",
    ),
    SlotSpec(
        id="site_area_m2", title="Площадь кровли / площадки", type=SlotType.NUMBER, unit="м²",
        weights={P.SOLAR_GRID: 0.8, P.SOLAR_HYBRID: 0.6},
        required_for=_({P.SOLAR_GRID, P.SOLAR_HYBRID}),
        ask="Какая примерно площадь?",
    ),
    SlotSpec(
        id="daytime_share", title="Доля дневного потребления", type=SlotType.CHOICE,
        choices=("high", "medium", "low", "unknown"), weights={P.SOLAR_GRID: 0.9, P.SOLAR_HYBRID: 0.5},
        required_for=_({P.SOLAR_GRID}),
        ask="Основное потребление у вас днём или вечером и ночью?",
        why="станция выгодна, когда выработка совпадает с нагрузкой",
        kb_source="20-products/23-solar › Лучший профиль клиента",
    ),
    SlotSpec(
        id="outages", title="Отключения внешней сети", type=SlotType.TEXT,
        weights={P.SOLAR_HYBRID: 1.0}, required_for=_({P.SOLAR_HYBRID}),
        gating_for=_({P.SOLAR_HYBRID}),
        ask="Бывают ли у вас отключения электричества и насколько они критичны?",
    ),
    SlotSpec(
        id="critical_load_kw", title="Критическая нагрузка", type=SlotType.NUMBER, unit="кВт",
        weights={P.SOLAR_HYBRID: 0.6}, ask="Какую нагрузку важно держать при отключении?",
    ),
)

ALL_SLOTS: tuple[SlotSpec, ...] = COMMON + OREM + METERING + PHOTO + LOSSES + SOLAR + CONTACT


@dataclass(frozen=True, slots=True)
class ContractRegistry:
    """Доступ к каталогу полей. Чистые функции, без IO."""

    slots: tuple[SlotSpec, ...] = ALL_SLOTS

    def __post_init__(self) -> None:
        ids = [s.id for s in self.slots]
        if len(ids) != len(set(ids)):
            raise ValueError("slot ids must be unique")

    def get(self, slot_id: str) -> SlotSpec:
        for spec in self.slots:
            if spec.id == slot_id:
                return spec
        raise KeyError(slot_id)

    def block(self, product: Product) -> tuple[SlotSpec, ...]:
        """Поля, важные для продукта (вес > 0), без общих и контактных."""
        return tuple(s for s in self.slots if s.weight(product) > 0 and not s.common and not s.contact)

    def active(self, candidates: Iterable[Product]) -> tuple[SlotSpec, ...]:
        """Активный контракт: общий блок + блоки кандидатов, без дублей, без контактов."""
        cands = tuple(candidates)
        return tuple(
            s for s in self.slots
            if not s.contact and (s.common or any(s.weight(p) > 0 for p in cands))
        )

    def required(self, product: Product) -> tuple[SlotSpec, ...]:
        return tuple(s for s in self.slots if product in s.required_for)

    def gating(self, product: Product) -> tuple[SlotSpec, ...]:
        return tuple(s for s in self.slots if product in s.gating_for)

    def contact_block(self) -> tuple[SlotSpec, ...]:
        return tuple(s for s in self.slots if s.contact)
