"""Фейковые порты для тестов application: детерминированные, без сети."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from energytool.application.build_index import KnowledgeIndex
from energytool.application.ports import ExternalServiceError
from energytool.domain.knowledge import Chunk, Scope, SectionKind, Topic
from energytool.domain.products import Product


def chunk(chunk_id: str, text: str, scope: Scope, kind: SectionKind = SectionKind.OTHER) -> Chunk:
    return Chunk(
        id=chunk_id,
        file=chunk_id.split("#")[0],
        path=chunk_id,
        text=text,
        scope=scope,
        kind=kind,
        tokens=len(text) // 4 + 1,
    )


CORPUS = (
    chunk("orem#порог", "порог оптового рынка 750 кВт мощность", Product.OREM, SectionKind.FITS),
    chunk("orem#нельзя", "нельзя обещать экономию", Product.OREM, SectionKind.FORBIDDEN),
    chunk("orem#не-подходит", "торговля офис не подходит", Product.OREM, SectionKind.NOT_FITS),
    chunk("solar#кровля", "кровля шифер панели солнечные", Product.SOLAR_GRID),
    chunk("solar#фасад", "фасад панели вертикальные", Product.SOLAR_GRID),
    chunk("askue#цеха", "учёт по цехам платформа счётчики", Product.METERING),
    chunk("energy#категории", "ценовая категория мощность счёт", Topic.ENERGY),
    chunk("general#специалист", "специалист свяжется", Topic.GENERAL, SectionKind.SPECIALIST),
)


def words(text: str) -> set[str]:
    return set(text.lower().split())


class FakeStore:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = {c.id: c for c in chunks}

    def all(self) -> Sequence[Chunk]:
        return list(self._chunks.values())

    def get(self, ids: Iterable[str]) -> Sequence[Chunk]:
        return [self._chunks[i] for i in ids if i in self._chunks]

    def where(
        self, scopes: frozenset[Scope] | None = None, kinds: frozenset[SectionKind] | None = None
    ) -> Sequence[Chunk]:
        return [
            c
            for c in self._chunks.values()
            if (scopes is None or c.scope in scopes) and (kinds is None or c.kind in kinds)
        ]


class OverlapLexical:
    """«BM25»: число общих слов."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = chunks

    def search(
        self, text: str, k: int, allowed: frozenset[str] | None = None
    ) -> list[tuple[str, float]]:
        scored = [
            (c.id, float(len(words(text) & words(c.text))))
            for c in self._chunks
            if allowed is None or c.id in allowed
        ]
        return sorted([s for s in scored if s[1] > 0], key=lambda s: -s[1])[:k]


class IdVectors:
    """«Dense»: вектор запроса = [индекс раздела], находит ровно его."""

    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._ids = [c.id for c in chunks]

    def search(
        self, vector: Sequence[float], k: int, allowed: frozenset[str] | None = None
    ) -> list[tuple[str, float]]:
        target = self._ids[int(vector[0])]
        return [(target, 1.0)] if allowed is None or target in allowed else []


class FakeEmbedder:
    def __init__(self, mapping: dict[str, int] | None = None, fail: bool = False) -> None:
        self.mapping = mapping or {}
        self.fail = fail
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise ExternalServiceError("embedder down")
        return [[float(self.mapping.get(t, 0))] for t in texts]


class OverlapReranker:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        self.calls.append((query, len(documents)))
        if self.fail:
            raise ExternalServiceError("reranker down")
        scored = [(i, len(words(query) & words(d)) / 10) for i, d in enumerate(documents)]
        return sorted(scored, key=lambda s: -s[1])[:top_n]


def toy_index(chunks: Sequence[Chunk] = CORPUS) -> KnowledgeIndex:
    return KnowledgeIndex(
        chunks=FakeStore(chunks), lexical=OverlapLexical(chunks), vectors=IdVectors(chunks)
    )
