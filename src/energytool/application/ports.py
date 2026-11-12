"""Порты application: всё, что use cases получают снаружи. Реализации — в infrastructure.

Типы только доменные (или DTO этого слоя). Никаких httpx, sqlite, numpy в сигнатурах.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from energytool.domain.extraction import Extraction
from energytool.domain.knowledge import Chunk, Scope, SectionKind
from energytool.domain.lead import Lead
from energytool.domain.reply import Reply, ReplyPlan
from energytool.domain.slots import SlotSpec
from energytool.domain.state import ConversationState, Message

if TYPE_CHECKING:
    from energytool.application.trace import TurnTrace


# --- LLM ---------------------------------------------------------------------------------------


class FactExtractor(Protocol):
    """LLM #1: реплики → факты в JSON с цитатами."""

    def extract(
        self, history: Sequence[Message], state: ConversationState, catalog: Sequence[SlotSpec]
    ) -> Extraction: ...


class ReplyWriter(Protocol):
    """LLM #2 (или шаблон): план ответа → текст."""

    def write(self, plan: ReplyPlan, history: Sequence[Message]) -> Reply: ...


# --- база знаний: модели ----------------------------------------------------------------------


class Embedder(Protocol):
    """bge-m3 через прокси."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    """Qwen3 Reranker 8B через прокси. (индекс документа, скор) по убыванию."""

    def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]: ...


# --- база знаний: хранилище и индексы ---------------------------------------------------------


class ChunkStore(Protocol):
    """Разделы базы и выборки по метаданным."""

    def all(self) -> Sequence[Chunk]: ...
    def get(self, ids: Iterable[str]) -> Sequence[Chunk]: ...
    def where(
        self, scopes: frozenset[Scope] | None = None, kinds: frozenset[SectionKind] | None = None
    ) -> Sequence[Chunk]: ...


class LexicalIndex(Protocol):
    """BM25. (chunk_id, score) по убыванию; allowed=None — весь корпус."""

    def search(
        self, text: str, k: int, allowed: frozenset[str] | None = None
    ) -> list[tuple[str, float]]: ...


class VectorIndex(Protocol):
    """Плотный поиск. (chunk_id, score) по убыванию."""

    def search(
        self, vector: Sequence[float], k: int, allowed: frozenset[str] | None = None
    ) -> list[tuple[str, float]]: ...


class IndexFactory(Protocol):
    """Строит хранилище и индексы из размеченных разделов. Сейчас in-memory + numpy + bm25s;
    позже FAISS/Chroma/внешний сервис — меняется только эта реализация."""

    def build(
        self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]
    ) -> tuple[ChunkStore, LexicalIndex, VectorIndex]: ...


# --- база знаний: источник и разметка (знание о структуре базы Energytool) ---------------------


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str  # "20-products/21-orem.md"
    text: str


@dataclass(frozen=True, slots=True)
class RawSection:
    file: str
    headings: tuple[str, ...]  # («Выход на ОРЭМ», «Кому подходит», «Порог доступа»)
    body: str


class KnowledgeSource(Protocol):
    """Чтение knowledge-base.zip (или каталога): только .md/.txt."""

    def load(self) -> Sequence[SourceFile]: ...


class SectionSplitter(Protocol):
    """Файл → разделы ## / ###."""

    def split(self, file: SourceFile) -> Sequence[RawSection]: ...


class SectionTagger(Protocol):
    """Раздел → (область, тип) по ручной карте и заголовкам."""

    def tag(self, section: RawSection) -> tuple[Scope, SectionKind]: ...


# --- сессии, заявки, телеметрия ---------------------------------------------------------------


class SessionRepo(Protocol):
    def load(self, session_id: str) -> ConversationState | None: ...
    def save(self, state: ConversationState, trace: TurnTrace) -> None: ...


class LeadSink(Protocol):
    def send(self, lead: Lead) -> None: ...


class Telemetry(Protocol):
    def record(self, trace: TurnTrace) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
