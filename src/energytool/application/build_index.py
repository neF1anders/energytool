"""Use case BuildKnowledgeIndex: индексация базы на старте процесса (веб и solution.py)."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

from energytool.application.ports import (
    ChunkStore,
    Embedder,
    ExternalServiceError,
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
    dense_ready: bool = True  # False — эмбеддер был недоступен при индексации, только BM25


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
        vectors, dense_ready = self._embed(chunks)
        store, lexical, dense = self.factory.build(chunks, vectors)
        index = KnowledgeIndex(store, lexical, dense, dense_ready)
        logger.info(
            "knowledge index built",
            extra={
                "data": {
                    "files": len(files),
                    "chunks": len(chunks),
                    "tokens": sum(c.tokens for c in chunks),
                    "dense_ready": dense_ready,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            },
        )
        return index

    def _embed(self, chunks: Sequence[Chunk]) -> tuple[list[list[float]], bool]:
        try:
            return self.embedder.embed([chunk.text for chunk in chunks]), True
        except ExternalServiceError as exc:
            logger.error("embedder unavailable at indexing, dense search disabled: %s", exc)
            return [[0.0] for _ in chunks], False


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
