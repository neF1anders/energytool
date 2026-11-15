"""Расширение найденных разделов соседями: поиск находит раздел, ответу нужен его контекст.

    ContextExpander(mode, chunks, max_tokens)(evidence) -> tuple[Evidence, ...]

Раздел базы — это заголовок ## / ### и несколько строк под ним. Эталонные ответы часто
собирают факты из соседних разделов того же документа («Данные для анализа» + «Что система
не делает»), а поиск по вопросу клиента приносит только один из них. Режимы:

- siblings — к каждому найденному разделу добавляются разделы с тем же родительским
  заголовком (##), кроме списков частых вопросов: там соседи — посторонние вопросы;
- file — добавляются все разделы документа, в котором лежит лучший найденный раздел.

Добавленные разделы получают origin="context" и идут в порядке документа; найденные поиском
разделы и их порядок не меняются. Модели не вызываются.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from energytool.application.build_index import PATH_SEPARATOR
from energytool.domain.knowledge import Chunk, Evidence, SectionKind

RANKED_ORIGINS = frozenset({"ranked", "fallback"})


class ExpandMode(StrEnum):
    NONE = "none"
    SIBLINGS = "siblings"
    FILE = "file"


@dataclass(frozen=True, slots=True)
class ContextExpander:
    mode: ExpandMode
    chunks: Sequence[Chunk]  # вся база в порядке документов
    max_tokens: int = 3000  # бюджет добавленных разделов

    def __call__(self, evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
        ranked = [e for e in evidence if e.origin in RANKED_ORIGINS]
        if self.mode is ExpandMode.NONE or not ranked:
            return tuple(evidence)
        neighbours = self._file_of(ranked[0]) if self.mode is ExpandMode.FILE else ()
        if self.mode is ExpandMode.SIBLINGS:
            neighbours = self._siblings_of(ranked)
        known = {e.chunk.id for e in evidence}
        return (*evidence, *self._within_budget(c for c in neighbours if c.id not in known))

    def _file_of(self, best: Evidence) -> tuple[Chunk, ...]:
        return tuple(c for c in self.chunks if c.file == best.chunk.file)

    def _siblings_of(self, ranked: Sequence[Evidence]) -> tuple[Chunk, ...]:
        parents = {
            parent
            for e in ranked
            if e.chunk.kind is not SectionKind.FAQ and (parent := _parent(e.chunk)) is not None
        }
        return tuple(c for c in self.chunks if _parent(c) in parents)

    def _within_budget(self, chunks: Iterable[Chunk]) -> tuple[Evidence, ...]:
        added: list[Evidence] = []
        tokens = 0
        for chunk in chunks:
            if tokens + chunk.tokens > self.max_tokens:
                break
            tokens += chunk.tokens
            added.append(Evidence(chunk=chunk, score=0.0, origin="context"))
        return tuple(added)


def _parent(chunk: Chunk) -> tuple[str, str] | None:
    """Родительский заголовок раздела ###: (файл, путь до ##). У раздела ## соседей нет."""
    headings = chunk.path.split(PATH_SEPARATOR)
    if len(headings) < 3:
        return None
    return chunk.file, PATH_SEPARATOR.join(headings[:2])
