"""Чтение questions.csv и запись submission.csv по регламенту (spec §9.3)."""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

QUESTION_FILES = ("questions.csv", "train.csv")
MAX_ANSWER_CHARS = 10_000
EMPTY_ANSWER = "Уточните, пожалуйста, ваш вопрос — и я подскажу или передам его специалисту."


class QuestionsNotFoundError(FileNotFoundError):
    """Нет questions.csv / train.csv в ожидаемых местах."""


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    text: str


def find_questions(roots: Iterable[Path]) -> Path:
    roots = tuple(roots)
    for root in roots:
        for name in QUESTION_FILES:
            if (root / name).is_file():
                return root / name
    raise QuestionsNotFoundError(f"questions.csv не найден в: {', '.join(map(str, roots))}")


def read_questions(path: Path) -> list[Question]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            Question(id=str(row.get("id", "")).strip(), text=str(row.get("question") or ""))
            for row in csv.DictReader(f)
        ]


def normalize_answer(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line[:MAX_ANSWER_CHARS] or EMPTY_ANSWER


def write_submission(path: Path, rows: Sequence[tuple[str, str]]) -> int:
    """Пишет уникальные id в исходном порядке; возвращает число строк."""
    seen: set[str] = set()
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow(["id", "answer"])
        for qid, answer in rows:
            if qid in seen:
                continue
            seen.add(qid)
            writer.writerow([qid, normalize_answer(answer)])
    os.replace(tmp, path)
    return len(seen)
