"""Образцы ответов консультанта для промпта: пары «вопрос — ответ» из примеров организаторов.

    load_style_examples(directory) -> tuple[StyleExample, ...]

Примеры — материалы хакатона, поэтому в репозитории их нет:
- при разработке они читаются из каталога STYLE_EXAMPLES_DIR (demo_questions.csv и
  demo_output.csv);
- в ZIP для платформы файлы данных класть нельзя, поэтому сборка
  (tools/build_submission.py --style-examples) вписывает пары в EMBEDDED.

Без каталога и без вписанных пар промпт остаётся прежним.
"""

from __future__ import annotations

import csv
from pathlib import Path

from energytool.domain.reply import StyleExample

QUESTIONS_FILE = "demo_questions.csv"
ANSWERS_FILE = "demo_output.csv"

EMBEDDED: tuple[tuple[str, str], ...] = ()


def load_style_examples(directory: Path | None) -> tuple[StyleExample, ...]:
    if directory is None:
        return tuple(StyleExample(q, a) for q, a in EMBEDDED)
    questions = _read(directory / QUESTIONS_FILE, "question")
    answers = _read(directory / ANSWERS_FILE, "answer")
    return tuple(
        StyleExample(text.strip(), answers[qid].strip())
        for qid, text in questions.items()
        if answers.get(qid, "").strip()
    )


def _read(path: Path, column: str) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["id"].strip(): row[column] for row in csv.DictReader(f)}
