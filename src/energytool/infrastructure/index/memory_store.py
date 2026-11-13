"""ChunkStore в памяти: ~235 разделов, выборки линейные — этого хватает с запасом."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from energytool.domain.knowledge import Chunk, Scope, SectionKind


class MemoryChunkStore:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = tuple(chunks)
        self._by_id = {chunk.id: chunk for chunk in self._chunks}
        if len(self._by_id) != len(self._chunks):
            raise ValueError("chunk ids must be unique")

    def all(self) -> Sequence[Chunk]:
        return self._chunks

    def get(self, ids: Iterable[str]) -> Sequence[Chunk]:
        """Неизвестные id пропускаются: pinned-разделы из domain не должны ронять ход."""
        return [self._by_id[i] for i in ids if i in self._by_id]

    def where(
        self,
        scopes: frozenset[Scope] | None = None,
        kinds: frozenset[SectionKind] | None = None,
    ) -> Sequence[Chunk]:
        return [
            chunk
            for chunk in self._chunks
            if (scopes is None or chunk.scope in scopes) and (kinds is None or chunk.kind in kinds)
        ]
