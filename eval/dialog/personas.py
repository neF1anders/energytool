"""Синтетические персоны для eval/dialog: легенда клиента и эталон анкеты.

Легенды придуманы для стенда (не данные хакатона). expected — значения, которые бот
должен извлечь из диалога; expected_leader — продукт, который должен вести к концу.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from energytool.domain.slots import SlotScalar


@dataclass(frozen=True, slots=True)
class Persona:
    id: str
    opening: str
    legend: str
    expected: dict[str, SlotScalar]
    expected_leader: str | None
    unknown: tuple[str, ...] = ()  # на эти вопросы клиент отвечает «не знаю»
    contact: str = "Иван Петров, +7 912 345-67-89, компания «Тест», на обработку данных согласен"
    excluded: tuple[str, ...] = field(default_factory=tuple)


PERSONAS: tuple[Persona, ...] = (
    Persona(
        id="bakery",
        opening="Здравствуйте, у нас хлебозавод, счета за свет за год выросли почти на треть.",
        legend=(
            "Ты главный инженер хлебозавода в Тульской области. Присоединённая мощность 1,1 МВт, "
            "потребление около 1,2 млн кВт·ч в месяц, работаете в три смены, электроэнергию "
            "покупаете у Тулаэнергосбыта. Ценовую категорию не знаешь. Хочешь снизить платёж."
        ),
        expected={
            "segment": "industry",
            "goal": "reduce_bill",
            "max_power_kw": 1100,
            "monthly_consumption_kwh": 1_200_000,
            "region": "Тульская",
            "current_supplier": "Тулаэнергосбыт",
        },
        expected_leader="orem",
        unknown=("price_category",),
        excluded=("losses",),
    ),
    Persona(
        id="warehouse_solar",
        opening="Думаем поставить солнечные панели на крышу склада, есть смысл?",
        legend=(
            "Ты управляющий складским комплексом в Краснодарском крае. Кровля из профнастила, "
            "около 6000 м², ей 4 года. Основное потребление днём, мощность 400 кВт. "
            "Отключений почти не бывает."
        ),
        expected={
            "segment": "warehouse",
            "site_kind": "roof",
            "roof_material": "profiled_sheet",
            "site_area_m2": 6000,
            "daytime_share": "high",
            "region": "Краснодар",
        },
        expected_leader="solar_grid",
        excluded=("orem",),
    ),
    Persona(
        id="retail_metering",
        opening="У нас сеть из 15 магазинов, хотим видеть расход по каждому онлайн.",
        legend=(
            "Ты финансовый директор сети магазинов в Москве и области: 15 точек, около 30 "
            "счётчиков, показания администраторы раз в месяц пишут в Excel. Модели счётчиков "
            "не помнишь. Хочешь сравнивать магазины и ловить перерасход."
        ),
        expected={
            "segment": "retail_chain",
            "sites_count": 15,
            "meters_count": 30,
            "goal": "metering",
        },
        expected_leader="metering",
        unknown=("meter_models",),
    ),
    Persona(
        id="grid_losses",
        opening="Мы электросетевая компания, хотим лучше находить хищения электроэнергии.",
        legend=(
            "Ты начальник службы учёта районной электросетевой компании в Тверской области. "
            "Около 20 тысяч точек учёта, данные почасовые по части приборов и суточные по "
            "остальным, архив за 2 года, выгрузку из системы учёта сделать можете."
        ),
        expected={
            "segment": "grid_company",
            "goal": "loss_detection",
            "archive_months": 24,
            "metering_export": True,
            "meters_count": 20_000,
        },
        expected_leader="losses",
    ),
    Persona(
        id="snt_housing",
        opening="Я председатель СНТ, не во всех домах есть счётчики и есть неплательщики.",
        legend=(
            "Ты председатель садового товарищества на 120 участков в Подмосковье. Счётчики "
            "стоят примерно у 80 участков, показания собираешь сам обходом. Хочешь удалённо "
            "отключать должников."
        ),
        expected={"segment": "housing", "meters_count": 80},
        expected_leader="metering",
    ),
    Persona(
        id="water_photo",
        opening="Мы водоканал, абоненты присылают показания как попало, хотим по фото.",
        legend=(
            "Ты руководитель абонентского отдела водоканала в Ярославле, около 50 тысяч "
            "абонентов, ресурс — вода. Сейчас показания принимаете по телефону и на сайте."
        ),
        expected={"segment": "resource_supply", "resource_type": "water"},
        expected_leader="photo",
    ),
    Persona(
        id="farm_hybrid",
        opening="На ферме часто отключают свет, коровник без электричества — беда. Что делать?",
        legend=(
            "Ты владелец молочной фермы в Вологодской области. Отключения внешней сети "
            "несколько раз в месяц по несколько часов, критичная нагрузка около 60 кВт. "
            "Есть свободный участок земли рядом."
        ),
        expected={
            "segment": "agro",
            "goal": "reliability",
            "critical_load_kw": 60,
            "site_kind": "ground",
        },
        expected_leader="solar_hybrid",
    ),
    Persona(
        id="small_office",
        opening="Мы ИТ-компания, офис 300 метров. Можно ли нам выйти на оптовый рынок?",
        legend=(
            "Ты офис-менеджер ИТ-компании в Казани, офис в бизнес-центре, мощность около "
            "40 кВт, производства нет. Платите арендодателю по счётчику."
        ),
        expected={"segment": "office_services", "max_power_kw": 40},
        expected_leader=None,
        excluded=("orem",),
    ),
    Persona(
        id="bakery_no_solar",
        opening="У нас хлебозавод, счета за электричество растут, начальник требует разобраться.",
        legend=(
            "Ты главный энергетик хлебозавода в Краснодарском крае: два предприятия, мощность "
            "490 кВт, 16 счётчиков, показания снимают вручную и присылают в WhatsApp, из-за "
            "этого ошибки в расчётах и непонятно, куда утекает электричество. Крыша большая, "
            "но солнечную станцию вы ставить не хотите: если консультант заговорит про солнце, "
            "площадку или кровлю, твёрдо скажи, что солнце вам не нужно, нужен порядок в учёте. "
            "Работаете днём."
        ),
        expected={
            "segment": "industry",
            "goal": "metering",
            "max_power_kw": 490,
            "meters_count": 16,
            "sites_count": 2,
            "meters_connectivity": "without",
        },
        expected_leader="metering",
        excluded=("orem", "losses"),
    ),
)
