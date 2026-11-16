"""Результат шага ① Extractor в терминах домена."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from energytool.domain.products import Product
from energytool.domain.slots import SlotScalar


class Intent(StrEnum):
    QUESTION = "question"  # клиент спрашивает → нужен поиск по базе
    INFO = "info"  # клиент сообщает факты / отвечает на вопрос анкеты
    MIXED = "mixed"  # и то и другое
    CONTACT = "contact"  # хочет связаться / оставляет контакты
    OUT_OF_SCOPE = "out_of_scope"  # вне компетенции → специалист
    SMALLTALK = "smalltalk"


@dataclass(frozen=True, slots=True)
class SlotUpdate:
    slot_id: str
    value: SlotScalar | None
    quote: str  # дословно из реплики клиента; без неё Reducer отбросит поле
    unit: str | None = None  # как сказал клиент («МВт»); Reducer приведёт к SlotSpec.unit


@dataclass(frozen=True, slots=True)
class Extraction:
    intent: Intent
    slot_updates: tuple[SlotUpdate, ...] = ()
    declined: tuple[str, ...] = ()  # slot_id, на которые клиент ответил «не знаю/не скажу»
    mentions: tuple[Product, ...] = ()  # продукты, о которых клиент спросил прямо
    wants_contact: bool = False
    search_query: str = ""  # самодостаточный запрос для поиска с учётом истории
    question: str = ""  # вопрос клиента, если есть, своими словами модели
    extras: dict[str, str] = field(default_factory=dict)  # отладка: сырой ответ, версия промпта
