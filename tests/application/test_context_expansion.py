from energytool.application.context_expansion import ContextExpander, ExpandMode
from energytool.domain.knowledge import Chunk, Evidence, SectionKind
from energytool.domain.products import Product


def _chunk(chunk_id: str, path: str, kind: SectionKind = SectionKind.OTHER) -> Chunk:
    return Chunk(
        id=chunk_id,
        file=chunk_id.split("#")[0],
        path=path,
        text=path,
        scope=Product.SOLAR_GRID,
        kind=kind,
        tokens=10,
    )


BASE = (
    _chunk("solar#что", "Станции › Что это"),
    _chunk("solar#диапазон", "Станции › Кому подходит › Диапазон"),
    _chunk("solar#профиль", "Станции › Кому подходит › Профиль"),
    _chunk("solar#faq-град", "Станции › Частые вопросы › Град", SectionKind.FAQ),
    _chunk("solar#faq-цена", "Станции › Частые вопросы › Цена", SectionKind.FAQ),
    _chunk("orem#порог", "Рынок › Кому подходит › Порог"),
)


def _found(chunk_id: str) -> Evidence:
    return Evidence(next(c for c in BASE if c.id == chunk_id), 0.9, "ranked")


def _added(evidence: tuple[Evidence, ...]) -> list[str]:
    return [e.chunk.id for e in evidence if e.origin == "context"]


def test_none_mode_returns_evidence_as_is() -> None:
    found = (_found("solar#диапазон"),)
    assert ContextExpander(ExpandMode.NONE, BASE)(found) == found


def test_siblings_share_parent_heading_and_keep_found_first() -> None:
    expanded = ContextExpander(ExpandMode.SIBLINGS, BASE)((_found("solar#диапазон"),))
    assert expanded[0].origin == "ranked"
    assert _added(expanded) == ["solar#профиль"]


def test_faq_and_top_level_sections_have_no_siblings() -> None:
    expander = ContextExpander(ExpandMode.SIBLINGS, BASE)
    assert _added(expander((_found("solar#faq-град"),))) == []
    assert _added(expander((_found("solar#что"),))) == []


def test_file_mode_adds_document_of_best_section_in_order() -> None:
    expanded = ContextExpander(ExpandMode.FILE, BASE)(
        (_found("solar#профиль"), _found("orem#порог"))
    )
    assert _added(expanded) == ["solar#что", "solar#диапазон", "solar#faq-град", "solar#faq-цена"]


def test_added_sections_respect_token_budget() -> None:
    expanded = ContextExpander(ExpandMode.FILE, BASE, max_tokens=25)((_found("solar#профиль"),))
    assert _added(expanded) == ["solar#что", "solar#диапазон"]
