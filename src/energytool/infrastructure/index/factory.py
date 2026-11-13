"""IndexFactory: in-memory хранилище + bm25s + numpy. FAISS/Chroma/внешний сервис —
другая реализация этого же порта, use cases не меняются (spec §7.1)."""

from __future__ import annotations

from collections.abc import Sequence

from energytool.application.ports import ChunkStore, LexicalIndex, VectorIndex
from energytool.domain.knowledge import Chunk
from energytool.infrastructure.index.bm25 import Bm25Index
from energytool.infrastructure.index.memory_store import MemoryChunkStore
from energytool.infrastructure.index.vectors import NumpyVectorIndex


class InMemoryIndexFactory:
    def build(
        self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]
    ) -> tuple[ChunkStore, LexicalIndex, VectorIndex]:
        return (
            MemoryChunkStore(chunks),
            Bm25Index(chunks),
            NumpyVectorIndex([c.id for c in chunks], vectors),
        )
