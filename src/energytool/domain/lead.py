"""Заявка и бриф менеджеру. Бриф собирается кодом из состояния, без пересказа LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from energytool.domain.products import Product
from energytool.domain.slots import SlotValue


@dataclass(frozen=True, slots=True)
class Contact:
    name: str
    phone: str
    company: str
    pd_consent: bool
    email: str | None = None
    position: str | None = None
    inn: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    product: Product
    probability: float
    reasons: tuple[str, ...]          # тексты сработавших правил
    flags: tuple[str, ...] = ()       # «пограничный: ниже 1 млн кВт·ч/мес»


@dataclass(frozen=True, slots=True)
class ExcludedSummary:
    product: Product
    reason: str                       # «поиск потерь: не для промышленных потребителей»
    kb_source: str


@dataclass(frozen=True, slots=True)
class Brief:
    session_id: str
    created_at: datetime
    contact: Contact
    pain: str
    candidates: tuple[CandidateSummary, ...]
    excluded: tuple[ExcludedSummary, ...]
    slots: tuple[SlotValue, ...]                  # в порядке пунктов опросного листа
    missing: tuple[str, ...]                      # slot_id обязательных, но пустых
    documents_to_prepare: tuple[str, ...]         # из DOCUMENT-полей и 03-calculation
    transcript_turns: int


@dataclass(frozen=True, slots=True)
class Lead:
    id: str
    brief: Brief
    text: str                         # человекочитаемый бриф
    payload: dict                     # JSON для CRM
