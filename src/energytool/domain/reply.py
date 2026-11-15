"""План ответа клиенту, который Responder превращает в текст."""

from __future__ import annotations

from dataclasses import dataclass

from energytool.domain.knowledge import Evidence
from energytool.domain.slots import SlotSpec
from energytool.domain.state import Message


@dataclass(frozen=True, slots=True)
class AskPlan:
    slot: SlotSpec  # что спросить (выбрал код)
    ask: str  # шаблон из реестра
    why: str  # короткое «зачем», часть фразы


@dataclass(frozen=True, slots=True)
class StyleExample:
    """Образец ответа консультанта: вопрос (или диалог с историей) и ответ."""

    question: str
    answer: str

    def is_about(self, question: str) -> bool:
        """Образец отвечает на этот же вопрос (последнюю реплику диалога)."""
        return bool(question.strip()) and _squash(self.question).endswith(_squash(question))


@dataclass(frozen=True, slots=True)
class StyleShot:
    """Образец вместе с разделами базы, найденными по его вопросу, — пример для промпта."""

    question: str
    history: tuple[Message, ...]
    evidence: tuple[Evidence, ...]
    answer: str


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    answer_question: str | None  # вопрос клиента, если он был
    evidence: tuple[Evidence, ...]  # факты для ответа (пусто, если поиска не было)
    ask: AskPlan | None  # ровно один вопрос или None
    offer_contact: bool
    acknowledge: tuple[
        str, ...
    ] = ()  # подтверждения из трассы правил: «1,1 МВт — выше порога 750 кВт»
    weak_evidence: bool = False  # поиск не нашёл близких разделов: отказ или специалист
    template_only: bool = False  # вопроса клиента нет → шаблон без LLM
    max_words: int = 70  # эталоны ~38 слов; лимит кейса 150
    shots: tuple[StyleShot, ...] = ()  # ближайшие образцы ответов как примеры в промпте


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    used_llm: bool
    evidence_ids: tuple[str, ...] = ()


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()
