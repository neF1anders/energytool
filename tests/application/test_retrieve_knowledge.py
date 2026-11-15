import pytest

from energytool.application.retrieval_stages import CONFIGS, get_config, parse_query
from energytool.application.retrieve_knowledge import RetrieveKnowledge
from energytool.domain.knowledge import (
    BoostSnippet,
    BudgetSnippet,
    IdSnippet,
    PinSnippet,
    ScopeSnippet,
    SectionKind,
    TextSnippet,
    Topic,
)
from energytool.domain.products import Product
from tests.application.fakes import FakeEmbedder, OverlapReranker, toy_index


def retrieve(
    config: str = "hybrid_rerank",
    embedder: FakeEmbedder | None = None,
    reranker: OverlapReranker | None = None,
) -> RetrieveKnowledge:
    return RetrieveKnowledge(
        get_config(config), toy_index(), embedder or FakeEmbedder(), reranker or OverlapReranker()
    )


def ids(result) -> list[str]:  # type: ignore[no-untyped-def]
    return [e.chunk.id for e in result.evidence]


def test_parse_query_collects_snippets_and_skips_blank_texts() -> None:
    parsed = parse_query(
        (
            TextSnippet("главный", weight=1.0),
            TextSnippet("второй", weight=0.5),
            TextSnippet("   "),
            TextSnippet("контекст", role="context"),
            ScopeSnippet(frozenset({Product.OREM})),
            BoostSnippet(Product.OREM, 1.2),
            IdSnippet(("a", "a", "b")),
            BudgetSnippet(top_k=2),
        )
    )
    assert [t.text for t in parsed.texts] == ["главный", "второй"]
    assert parsed.rerank_query == "главный\nконтекст"
    assert parsed.scopes == frozenset({Product.OREM})
    assert parsed.ids == ("a", "b")
    assert parsed.budget.top_k == 2


def test_hybrid_rerank_finds_relevant_section_first() -> None:
    result = retrieve()((TextSnippet("шифер кровля панели"), BudgetSnippet(top_k=2)))
    assert ids(result)[0] == "solar#кровля"
    assert all(e.origin == "ranked" for e in result.evidence)
    assert set(result.timings_ms) >= {"recall", "fuse", "rerank", "pack", "total"}
    assert result.scope_mass[Product.SOLAR_GRID] == 1.0


def test_scope_limits_search_and_fallback_adds_strong_outside_hit() -> None:
    query = (
        TextSnippet("учёт по цехам платформа"),
        ScopeSnippet(frozenset({Product.OREM})),
        BudgetSnippet(top_k=2, fallback_k=1),
    )
    result = retrieve()(query)
    origins = {e.chunk.id: e.origin for e in result.evidence}
    assert origins["askue#цеха"] == "fallback"
    assert all(e.chunk.scope is Product.OREM for e in result.evidence if e.origin == "ranked")


def test_by_id_and_pins_come_first_and_without_search() -> None:
    embedder, reranker = FakeEmbedder(), OverlapReranker()
    query = (
        IdSnippet(("general#специалист", "missing#id")),
        PinSnippet(Product.OREM, frozenset({SectionKind.FORBIDDEN, SectionKind.NOT_FITS})),
    )
    result = retrieve(embedder=embedder, reranker=reranker)(query)
    assert ids(result) == ["general#специалист", "orem#нельзя", "orem#не-подходит"]
    assert [e.origin for e in result.evidence] == ["by_id", "pinned", "pinned"]
    assert embedder.calls == 0
    assert reranker.calls == []


def test_dense_failure_degrades_to_lexical() -> None:
    result = retrieve(embedder=FakeEmbedder(fail=True))((TextSnippet("ценовая категория"),))
    assert ids(result)[0] == "energy#категории"
    assert "dense_failed" in result.trace["notes"]  # type: ignore[operator]


def test_rerank_failure_degrades_to_fused() -> None:
    result = retrieve(reranker=OverlapReranker(fail=True))((TextSnippet("ценовая категория"),))
    assert ids(result)[0] == "energy#категории"
    assert "rerank_failed" in result.trace["notes"]  # type: ignore[operator]
    assert result.below_floor is False


def test_below_floor_when_best_rerank_score_is_low() -> None:
    query = (TextSnippet("кровля"), BudgetSnippet(min_rerank_score=0.5))
    assert retrieve()(query).below_floor is True


def test_boost_lifts_leader_scope() -> None:
    query = (TextSnippet("мощность"), BoostSnippet(Topic.ENERGY, 10.0), BudgetSnippet(top_k=1))
    assert ids(retrieve("hybrid")(query)) == ["energy#категории"]


def test_dense_only_uses_embedder_mapping() -> None:
    embedder = FakeEmbedder({"что угодно": 4})
    result = retrieve("dense", embedder=embedder)(
        (TextSnippet("что угодно"), BudgetSnippet(top_k=1))
    )
    assert ids(result) == ["solar#фасад"]


def test_token_budget_keeps_first_and_cuts_rest() -> None:
    result = retrieve()((TextSnippet("панели кровля фасад шифер"), BudgetSnippet(max_tokens=1)))
    assert len(result.evidence) == 1


def test_bm25_config_does_not_call_models() -> None:
    embedder, reranker = FakeEmbedder(), OverlapReranker()
    retrieve("bm25", embedder, reranker)((TextSnippet("панели"),))
    assert embedder.calls == 0
    assert reranker.calls == []


def test_unknown_config_lists_available() -> None:
    with pytest.raises(KeyError, match="hybrid_rerank"):
        get_config("nope")
    assert "bm25" in CONFIGS


def test_weak_sections_are_dropped_relative_to_best_rerank_score() -> None:
    text = TextSnippet("порог оптового рынка мощность")
    everything = ids(retrieve()((text, BudgetSnippet(top_k=4, fallback_k=0))))
    strong = ids(retrieve()((text, BudgetSnippet(top_k=4, fallback_k=0, keep_ratio=0.9))))
    assert len(everything) > 1
    assert strong == ["orem#порог"]


def test_weak_sections_stay_when_reranker_failed() -> None:
    query = (TextSnippet("порог оптового рынка мощность"), BudgetSnippet(top_k=4, keep_floor=0.9))
    result = retrieve(reranker=OverlapReranker(fail=True))(query)
    assert len(ids(result)) > 1
