"""KnowledgeSource: knowledge-base.zip (без распаковки) или каталог с .md/.txt."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

from energytool.application.ports import SourceFile

logger = logging.getLogger(__name__)

ARCHIVE_NAMES = ("knowledge-base.zip", "knowledge_base.zip", "kb.zip")
DIRECTORY_NAMES = ("knowledge-base", "knowledge_base", "kb")
_SUFFIXES = frozenset({".md", ".txt"})


class KnowledgeBaseNotFoundError(FileNotFoundError):
    """Ни архива, ни каталога базы знаний в ожидаемых местах."""


def find_knowledge_base(roots: Iterable[Path]) -> Path:
    """Первый найденный архив (приоритет) или каталог в порядке roots."""
    roots = tuple(roots)
    for root in roots:
        for name in ARCHIVE_NAMES:
            if (root / name).is_file():
                return root / name
    for root in roots:
        for name in DIRECTORY_NAMES:
            if (root / name).is_dir():
                return root / name
    searched = ", ".join(str(r) for r in roots)
    raise KnowledgeBaseNotFoundError(f"база знаний не найдена в: {searched}")


class FileKnowledgeSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Sequence[SourceFile]:
        files = self._load_zip() if self._path.is_file() else self._load_dir()
        files = sorted((f for f in files if f.text.strip()), key=lambda f: f.path)
        if not files:
            raise KnowledgeBaseNotFoundError(f"{self._path}: нет файлов .md/.txt")
        logger.info(
            "knowledge base loaded",
            extra={"data": {"path": str(self._path), "files": len(files)}},
        )
        return files

    def _load_zip(self) -> list[SourceFile]:
        with zipfile.ZipFile(self._path) as archive:
            return [
                SourceFile(path=_normalize(name), text=_decode(archive.read(name)))
                for name in archive.namelist()
                if _is_document(PurePosixPath(name))
            ]

    def _load_dir(self) -> list[SourceFile]:
        return [
            SourceFile(
                path=path.relative_to(self._path).as_posix(), text=_decode(path.read_bytes())
            )
            for path in self._path.rglob("*")
            if path.is_file() and _is_document(PurePosixPath(path.relative_to(self._path)))
        ]


def _is_document(path: PurePosixPath) -> bool:
    return (
        path.suffix.lower() in _SUFFIXES
        and "__MACOSX" not in path.parts
        and not path.name.startswith(".")
    )


def _normalize(name: str) -> str:
    """Убирает внешнюю папку архива (knowledge-base/20-products/x.md → 20-products/x.md),
    чтобы id разделов не зависели от того, как упакован архив."""
    parts = PurePosixPath(name).parts
    if len(parts) > 2 and parts[0] in DIRECTORY_NAMES:
        parts = parts[1:]
    return "/".join(parts)


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
