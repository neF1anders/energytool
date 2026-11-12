"""Use case BuildKnowledgeIndex: индексация базы на старте процесса (веб и solution.py).

    load → split → tag → embed → build

Источник, нарезка и разметка — порты (структуру базы Energytool знает infrastructure/kb).
Результат — KnowledgeIndex, который получает RetrieveKnowledge.
"""

from __future__ import annotations

from dataclasses import dataclass

from energytool.application.ports import (
    ChunkStore,
    Embedder,
    IndexFactory,
    KnowledgeSource,
    LexicalIndex,
    SectionSplitter,
    SectionTagger,
    VectorIndex,
)


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
    embed_batch: int = 32

    def __call__(self) -> KnowledgeIndex:
        """Шаги:
        1. files = source.load()
        2. sections = splitter.split(f) для каждого файла
        3. Chunk(id=f"{file}#{slug(path)}", path=" › ".join(headings),
                 text=path + "\\n" + body, scope/kind=tagger.tag(section), tokens=…)
        4. vectors = embedder.embed(texts) батчами по embed_batch
        5. KnowledgeIndex(*factory.build(chunks, vectors))
        """
        raise NotImplementedError
