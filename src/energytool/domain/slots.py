"""Поле анкеты (слот): описание в реестре и значение в состоянии сессии."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from energytool.domain.products import Product


class SlotType(StrEnum):
    TEXT = "text"
    NUMBER = "number"  # всегда в базовой единице SlotSpec.unit
    INTEGER = "integer"
    BOOL = "bool"
    CHOICE = "choice"  # одно значение из SlotSpec.choices
    DOCUMENT = "document"  # наличие документа: см. DocumentStatus


class DocumentStatus(StrEnum):
    """Документы бот не собирает, а отмечает их наличие у клиента."""

    HAVE = "have"
    NO = "no"
    LATER = "later"  # «уточню», «соберу с бухгалтерией»


class SlotStatus(StrEnum):
    FILLED = "filled"
    DECLINED = "declined"  # «не знаю» / «не скажу» — считается закрытым для порога READY
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class SlotSpec:
    """Описание поля в Contract Registry. Единый источник правды для

    Extractor (JSON-схема), Reducer (тип, единицы), Policy (вес, обязательность),
    Responder (шаблон вопроса) и Brief (порядок полей как в опросном листе).
    """

    id: str
    title: str  # подпись в брифе
    type: SlotType
    ask: str  # шаблон вопроса клиенту
    why: str = ""  # короткое «зачем спрашиваем», часть фразы
    unit: str | None = None  # базовая единица (кВт, кВт·ч/мес, м²)
    choices: tuple[str, ...] = ()
    weights: Mapping[Product, float] = field(default_factory=dict)  # важность поля для продукта
    required_for: frozenset[Product] = frozenset()
    gating_for: frozenset[Product] = frozenset()  # без него нельзя решить, подходит ли продукт
    common: bool = False  # общий блок, спрашиваем независимо от лидера
    contact: bool = False  # спрашиваем только в CONTACT
    opros_item: int | None = None  # пункт «Опросного листа ОРЭМ»
    kb_source: str | None = None  # раздел базы, откуда взято поле

    def weight(self, product: Product) -> float:
        return self.weights.get(product, 0.0)


SlotScalar = str | float | int | bool


@dataclass(frozen=True, slots=True)
class SlotValue:
    """Значение поля в состоянии. Неизменяемое: правка = новый SlotValue."""

    slot_id: str
    value: SlotScalar | None
    status: SlotStatus
    quote: str  # дословная цитата клиента, подтверждённая Reducer
    turn: int  # номер хода, на котором записано
    previous: SlotValue | None = None  # история правок
