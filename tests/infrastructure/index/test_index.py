import pytest

from energytool.domain.knowledge import SectionKind, Topic
from energytool.domain.products import Product
from energytool.infrastructure.index.bm25 import Bm25Index, tokenize
from energytool.infrastructure.index.factory import InMemoryIndexFactory
from energytool.infrastructure.index.memory_store import MemoryChunkStore
from energytool.infrastructure.index.vectors import NumpyVectorIndex
from tests.application.fakes import CORPUS, chunk


def test_tokenize_normalizes_yo_stems_and_drops_stopwords() -> None:
    assert tokenize("Счётчики и СЧЕТЧИКОВ для 750 кВт") == ["счетчи", "счетчи", "750", "квт"]


def test_bm25_ranks_and_respects_allowed() -> None:
    index = Bm25Index(CORPUS)
    assert index.search("шиферная кровля", 3)[0][0] == "solar#кровля"
    allowed = frozenset({"askue#цеха", "solar#фасад"})
    assert [i for i, _ in index.search("панели кровля", 5, allowed)] == ["solar#фасад"]


def test_bm25_unknown_words_return_nothing() -> None:
    assert Bm25Index(CORPUS).search("абракадабра", 5) == []


def test_vectors_cosine_and_allowed() -> None:
    index = NumpyVectorIndex(["a", "b", "c"], [[1, 0], [0, 1], [0.7, 0.7]])
    assert [i for i, _ in index.search([1, 0.1], 2)] == ["a", "c"]
    assert [i for i, _ in index.search([1, 0], 5, frozenset({"b"}))] == ["b"]
    assert index.search([1, 0], 5, frozenset()) == []


def test_vectors_length_mismatch() -> None:
    with pytest.raises(ValueError):
        NumpyVectorIndex(["a"], [])


def test_store_filters_and_skips_unknown_ids() -> None:
    store = MemoryChunkStore(CORPUS)
    assert [c.id for c in store.get(["orem#нельзя", "x"])] == ["orem#нельзя"]
    forbidden = store.where(frozenset({Product.OREM}), frozenset({SectionKind.FORBIDDEN}))
    assert [c.id for c in forbidden] == ["orem#нельзя"]
    assert len(store.where(scopes=frozenset({Topic.ENERGY}))) == 1


def test_store_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        MemoryChunkStore([chunk("a", "x", Topic.GENERAL), chunk("a", "y", Topic.GENERAL)])


def test_factory_builds_all_three() -> None:
    store, lexical, vectors = InMemoryIndexFactory().build(CORPUS, [[1.0, 0.0]] * len(CORPUS))
    assert len(store.all()) == len(CORPUS)
    assert lexical.search("кровля", 1)
    assert vectors.search([1.0, 0.0], 1)
