"""Состояние сессии: одно на диалог, общий блок + блоки всех 6 продуктов."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from energytool.domain.products import Product
from energytool.domain.slots import SlotScalar, SlotStatus, SlotValue


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    text: str
    turn: int


class DialogState(StrEnum):
    """Конечный автомат диалога (см. docs/diagrams/dialog-state.mmd)."""

    OPEN = "open"  # приветствие
    DISCOVERY = "discovery"  # лидера нет: спрашиваем различающие поля
    QUALIFY = "qualify"  # есть лидер или два кандидата: поля их блоков
    READY = "ready"  # порог полноты пройден: предлагаем заявку
    CONTACT = "contact"  # собираем ФИО, телефон, компанию, согласие
    NO_CONTACT = "no_contact"  # клиент отказался: отвечаем, даём «что подготовить»
    SUBMITTED = "submitted"  # заявка и бриф отправлены


@dataclass(frozen=True, slots=True)
class SoftSignal:
    """Мягкий сигнал интереса к продукту. Затухает: weight · λ^(T − turn)."""

    product: Product
    weight: float
    turn: int
    source: str  # "mention" | "retrieval_mass" | ...


@dataclass(slots=True)
class ConversationState:
    session_id: str
    turn: int = 0
    dialog: DialogState = DialogState.OPEN
    slots: dict[str, SlotValue] = field(default_factory=dict)
    soft_signals: list[SoftSignal] = field(default_factory=list)
    asked: dict[str, int] = field(default_factory=dict)  # slot_id → сколько раз спросили
    last_cta_turn: int | None = None  # когда последний раз предлагали заявку
    turns_without_progress: int = 0
    leader_history: list[Product | None] = field(default_factory=list)  # для гистерезиса
    history: list[Message] = field(default_factory=list)

    def value(self, slot_id: str) -> SlotScalar | None:
        sv = self.slots.get(slot_id)
        return sv.value if sv and sv.status is SlotStatus.FILLED else None

    def is_closed(self, slot_id: str) -> bool:
        """Заполнено или клиент отказался — для порога READY оба считаются закрытыми."""
        sv = self.slots.get(slot_id)
        return sv is not None and sv.status in (SlotStatus.FILLED, SlotStatus.DECLINED)
