"""SectionSplitter для markdown базы Energytool: раздел = заголовок # / ## / ### и текст
до следующего заголовка любого уровня.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from energytool.application.ports import RawSection, SourceFile

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


class MarkdownSectionSplitter:
    def split(self, file: SourceFile) -> Sequence[RawSection]:
        sections: list[RawSection] = []
        path: list[str] = []
        body: list[str] = []
        in_code = False

        def flush() -> None:
            text = "\n".join(body).strip()
            if text:
                headings = tuple(path) or (_title_from_file(file.path),)
                sections.append(RawSection(file=file.path, headings=headings, body=text))
            body.clear()

        for line in file.text.splitlines():
            if line.lstrip().startswith("```"):
                in_code = not in_code
            match = None if in_code else _HEADING.match(line)
            if match is None:
                body.append(line)
                continue
            flush()
            level = len(match.group(1))
            del path[level - 1 :]
            path.extend([""] * (level - 1 - len(path)))
            path.append(match.group(2).strip())
        flush()
        return [_drop_empty_levels(s) for s in sections]


def _drop_empty_levels(section: RawSection) -> RawSection:
    """Пропущенный уровень (### сразу под #) не оставляет пустых элементов пути."""
    headings = tuple(h for h in section.headings if h)
    return RawSection(file=section.file, headings=headings, body=section.body)


def _title_from_file(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
