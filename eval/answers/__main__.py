"""Стенд ответов: `uv run --env-file .env python -m eval.answers --team <submission.csv>`.

Сравнивает ответы команды с baseline (по умолчанию eval/data/baseline/submission.csv —
учебный пример организаторов) на демо-вопросах с эталонами. Отчёт с причинами судьи —
в eval/data/results/.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from energytool.bootstrap import build
from eval.answers.judge import JUDGE_VERSION, QuestionScore, combine, judge_pair, leaderboard
from eval.common import EVAL_DATA, DemoItem, load_demo, read_csv, results_path

DEFAULT_BASELINE = EVAL_DATA / "baseline" / "submission.csv"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.answers")
    parser.add_argument("--team", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    container = build()
    team = read_csv(args.team, "answer")
    baseline = read_csv(args.baseline, "answer")
    items = [i for i in load_demo() if i.id in team and i.id in baseline and i.golden]

    def run(item: DemoItem) -> QuestionScore:
        rounds = judge_pair(
            container.gateway, item.question, item.golden, team[item.id], baseline[item.id]
        )
        return combine(item.id, rounds)

    with ThreadPoolExecutor(max_workers=container.settings.max_workers) as pool:
        scores: list[QuestionScore] = list(pool.map(run, items))
    board = leaderboard(scores)
    lines = [
        f"# Answers eval {args.label}".rstrip(),
        "",
        f"judge: `{JUDGE_VERSION}`, team: `{args.team}`, baseline: `{args.baseline}`",
        "",
        "| n | Score | MatchRate | WinRate | W/L/T | avg words |",
        "|---|---|---|---|---|---|",
        f"| {board.n} | {board.score:.3f} | {board.match_rate:.3f} | {board.win_rate:.3f} | "
        f"{board.wins}/{board.losses}/{board.ties} | "
        f"{sum(len(team[i.id].split()) for i in items) / max(len(items), 1):.0f} |",
        "",
        "## По вопросам",
        "",
    ]
    for s in scores:
        reasons = " / ".join(r.reason for r in s.rounds if r is not None)
        lines.append(f"- **{s.qid}**: {s.score} — {reasons}")
    report = "\n".join(lines) + "\n"
    print(report.split("## По вопросам")[0])
    path = results_path("answers")
    path.write_text(report, encoding="utf-8")
    print(f"отчёт: {path}")
    container.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
