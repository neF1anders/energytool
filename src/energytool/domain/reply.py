"""План ответа клиенту, который Responder превращает в текст."""

from __future__ import annotations

from dataclasses import dataclass

from energytool.domain.knowledge import Evidence
from energytool.domain.slots import SlotSpec


@dataclass(frozen=True, slots=True)
class AskPlan:
    slot: SlotSpec  # что спросить (выбрал код)
    ask: str  # шаблон из реестра
    why: str  # короткое «зачем», часть фразы


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    answer_question: str | None  # вопрос клиента, если он был
    evidence: tuple[Evidence, ...]  # факты для ответа (пусто, если поиска не было)
    ask: AskPlan | None  # ровно один вопрос или None
    offer_contact: bool
    acknowledge: tuple[
        str, ...
    ] = ()  # подтверждения из трассы правил: «1,1 МВт — выше порога 750 кВт»
    template_only: bool = False  # вопроса клиента нет → шаблон без LLM
    max_words: int = 70  # эталоны ~38 слов; лимит кейса 150


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    used_llm: bool
    evidence_ids: tuple[str, ...] = ()
