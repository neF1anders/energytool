"""Общее для стендов eval: пути и загрузка демо-набора.

Данные eval — производные материалов хакатона, поэтому живут в eval/data/ (в .gitignore).
Код стендов в git, наборы и результаты — только локально.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
EVAL_DATA = REPO / "eval" / "data"
MATERIALS_DATA = REPO / "materials" / "data"
KNOWLEDGE_BASE = MATERIALS_DATA / "knowledge-base.zip"
DEMO_QUESTIONS = MATERIALS_DATA / "demo_questions.csv"
DEMO_GOLDEN = MATERIALS_DATA / "demo_output.csv"


@dataclass(frozen=True, slots=True)
class DemoItem:
    id: str
    question: str
    golden: str


def read_csv(path: Path, value_column: str) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["id"].strip(): row[value_column] for row in csv.DictReader(f)}


def load_demo() -> list[DemoItem]:
    questions = read_csv(DEMO_QUESTIONS, "question")
    golden = read_csv(DEMO_GOLDEN, "answer")
    return [DemoItem(qid, text, golden.get(qid, "")) for qid, text in questions.items()]


def load_extra(path: Path) -> list[DemoItem]:
    """Дополнительный локальный набор: [{"id", "question", "golden"}, ...]."""
    return [DemoItem(str(i["id"]), i["question"], i["golden"]) for i in load_json(path, [])]


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def results_path(kind: str, suffix: str = "md") -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = EVAL_DATA / "results" / f"{kind}-{stamp}.{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
