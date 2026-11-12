"""Use case BuildKnowledgeIndex: индексация базы на старте процесса (веб и solution.py).

    load → split → tag → embed → build

Источник, нарезка и разметка — порты (структуру базы Energytool знает infrastructure/kb).
Результат — KnowledgeIndex, который получает RetrieveKnowledge.

Id раздела = «файл#slug(путь без заголовка файла)», стабилен между запусками: на нём
держатся разметка eval и ссылки на разделы из domain (pinned_sections).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

from energytool.application.ports import (
    ChunkStore,
    Embedder,
    IndexFactory,
    KnowledgeSource,
    LexicalIndex,
    RawSection,
    SectionSplitter,
    SectionTagger,
    VectorIndex,
)
from energytool.domain.knowledge import Chunk

logger = logging.getLogger(__name__)

PATH_SEPARATOR = " › "
CHARS_PER_TOKEN = 3.5  # оценка для русского текста без токенизатора (spec §7.4)


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def slugify(text: str) -> str:
    words = re.findall(r"[0-9a-zа-яё]+", text.lower())
    return "-".join(words)


@dataclass(frozen=True, slots=True)
class KnowledgeIndex:
    chunks: ChunkStore
    lexical: LexicalIndex
    vectors: VectorIndex


@dataclass(frozen=True, slots=True)
class BuildKnowledgeIndex:
    source: KnowledgeSource
    splitter: SectionSplitter
    tagger: SectionTagger
    embedder: Embedder
    factory: IndexFactory

    def __call__(self) -> KnowledgeIndex:
        started = time.perf_counter()
        files = self.source.load()
        sections = [section for file in files for section in self.splitter.split(file)]
        chunks = make_chunks(sections, self.tagger)
        vectors = self.embedder.embed([chunk.text for chunk in chunks])
        store, lexical, dense = self.factory.build(chunks, vectors)
        logger.info(
            "knowledge index built",
            extra={
                "data": {
                    "files": len(files),
                    "chunks": len(chunks),
                    "tokens": sum(c.tokens for c in chunks),
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            },
        )
        return KnowledgeIndex(chunks=store, lexical=lexical, vectors=dense)


def make_chunks(sections: Sequence[RawSection], tagger: SectionTagger) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: dict[str, int] = {}
    for section in sections:
        path = PATH_SEPARATOR.join(section.headings)
        anchor = "/".join(slugify(h) for h in section.headings[1:]) or "intro"
        base_id = f"{section.file}#{anchor}"
        seen[base_id] = seen.get(base_id, 0) + 1
        chunk_id = base_id if seen[base_id] == 1 else f"{base_id}-{seen[base_id]}"
        scope, kind = tagger.tag(section)
        text = f"{path}\n{section.body}"
        chunks.append(
            Chunk(
                id=chunk_id,
                file=section.file,
                path=path,
                text=text,
                scope=scope,
                kind=kind,
                tokens=estimate_tokens(text),
            )
        )
    return chunks
