import zipfile
from pathlib import Path

import pytest

from energytool.application.ports import RawSection, SourceFile
from energytool.domain.knowledge import SectionKind, Topic
from energytool.domain.products import Product
from energytool.infrastructure.kb.loader import (
    FileKnowledgeSource,
    KnowledgeBaseNotFoundError,
    find_knowledge_base,
)
from energytool.infrastructure.kb.splitter import MarkdownSectionSplitter
from energytool.infrastructure.kb.tagmap import KbSectionTagger

OREM_MD = """# Выход на ОРЭМ

Вступление.

## Кому подходит

### Порог доступа

От 750 кВт.

### Не подходит, если

Мощность меньше.

## Пример

```
# не заголовок
```

## Частые вопросы

### Сколько это стоит

Зависит от объекта.
"""


def test_splitter_keeps_heading_path_and_drops_empty_sections() -> None:
    sections = MarkdownSectionSplitter().split(SourceFile("20-products/21-orem.md", OREM_MD))
    assert [s.headings for s in sections] == [
        ("Выход на ОРЭМ",),
        ("Выход на ОРЭМ", "Кому подходит", "Порог доступа"),
        ("Выход на ОРЭМ", "Кому подходит", "Не подходит, если"),
        ("Выход на ОРЭМ", "Пример"),
        ("Выход на ОРЭМ", "Частые вопросы", "Сколько это стоит"),
    ]
    assert sections[1].body == "От 750 кВт."
    assert "# не заголовок" in sections[3].body


def test_splitter_names_headless_file_by_its_stem() -> None:
    (section,) = MarkdownSectionSplitter().split(SourceFile("notes/readme.txt", "просто текст"))
    assert section.headings == ("readme",)


@pytest.mark.parametrize(
    ("file", "headings", "scope", "kind"),
    [
        ("20-products/21-orem.md", ("ОРЭМ", "Кому подходит", "Порог"), Product.OREM, "fits"),
        (
            "20-products/21-orem.md",
            ("ОРЭМ", "Кому подходит", "Не подходит, если"),
            Product.OREM,
            "not_fits",
        ),
        (
            "20-products/23-solar.md",
            ("СЭС", "Чего утверждать нельзя"),
            Product.SOLAR_GRID,
            "forbidden",
        ),
        (
            "20-products/22-askue.md",
            ("Платформа", "Частые вопросы", "Цена"),
            Product.METERING,
            "faq",
        ),
        (
            "10-energy/11-price-categories.md",
            ("Категории", "Шесть категорий"),
            Topic.ENERGY,
            "other",
        ),
        (
            "10-energy/12-power-thresholds.md",
            ("Пороги", "Пороги оптового рынка"),
            Product.OREM,
            "other",
        ),
        ("10-energy/15-microgeneration.md", ("Микро", "Что это"), Product.SOLAR_GRID, "other"),
        (
            "00-reference/01-numbers-and-indicators.md",
            ("Числа", "Платформа учёта"),
            Product.METERING,
            "numbers",
        ),
        (
            "00-reference/02-product-scope-and-limits.md",
            ("Границы", "Когда требуется специалист"),
            Topic.GENERAL,
            "specialist",
        ),
        (
            "00-reference/03-calculation-and-requests.md",
            ("Расчёт", "Сведения", "Для солнечной станции"),
            Product.SOLAR_GRID,
            "requirements",
        ),
        ("99-new/unknown.md", ("Новое",), Topic.GENERAL, "other"),
    ],
)
def test_tagger_map(file: str, headings: tuple[str, ...], scope: object, kind: str) -> None:
    assert KbSectionTagger().tag(RawSection(file, headings, "текст")) == (scope, SectionKind(kind))


def test_loader_reads_zip_with_outer_folder_and_skips_junk(tmp_path: Path) -> None:
    archive = tmp_path / "kb.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("knowledge-base/20-products/21-orem.md", "﻿# ОРЭМ\r\nтекст")
        zf.writestr("knowledge-base/img.png", b"\x89PNG")
        zf.writestr("__MACOSX/knowledge-base/._x.md", "мусор")
        zf.writestr("knowledge-base/empty.md", "   ")
    files = FileKnowledgeSource(archive).load()
    assert files == [SourceFile("20-products/21-orem.md", "# ОРЭМ\nтекст")]


def test_loader_reads_directory(tmp_path: Path) -> None:
    (tmp_path / "10-energy").mkdir()
    (tmp_path / "10-energy" / "17-faq.md").write_text("# FAQ", encoding="utf-8")
    assert [f.path for f in FileKnowledgeSource(tmp_path).load()] == ["10-energy/17-faq.md"]


def test_find_prefers_archive_over_directory(tmp_path: Path) -> None:
    (tmp_path / "knowledge-base").mkdir()
    (tmp_path / "knowledge_base.zip").write_bytes(b"")
    assert find_knowledge_base([tmp_path]).name == "knowledge_base.zip"


def test_find_reports_searched_roots(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeBaseNotFoundError, match=str(tmp_path)):
        find_knowledge_base([tmp_path])
