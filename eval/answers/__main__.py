"""Стенд ответов: `uv run --env-file .env python -m eval.answers --team <submission.csv>`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from energytool.bootstrap import build
from eval.answers.judge import (
    JUDGE_PROMPTS,
    JUDGE_VERSION,
    Leaderboard,
    QuestionScore,
    combine,
    judge_pair,
    leaderboard,
)
from eval.common import EVAL_DATA, DemoItem, load_demo, load_extra, read_csv, results_path

DEFAULT_BASELINE = EVAL_DATA / "baseline" / "submission.csv"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.answers")
    parser.add_argument("--team", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--label", default="")
    parser.add_argument("--extra", type=Path, help="доп. набор вопросов с эталонами (json)")
    parser.add_argument("--judge", choices=sorted(JUDGE_PROMPTS), default="v1")
    args = parser.parse_args(argv)

    container = build()
    team = read_csv(args.team, "answer")
    baseline = read_csv(args.baseline, "answer")
    dataset = load_demo() + (load_extra(args.extra) if args.extra else [])
    items = [i for i in dataset if i.id in team and i.id in baseline and i.golden]
    system = JUDGE_PROMPTS[args.judge]

    def run(item: DemoItem) -> QuestionScore:
        rounds = judge_pair(
            container.gateway,
            item.question,
            item.golden,
            team[item.id],
            baseline[item.id],
            system,
        )
        return combine(item.id, rounds)

    with ThreadPoolExecutor(max_workers=container.settings.max_workers) as pool:
        scores: list[QuestionScore] = list(pool.map(run, items))
    demo_ids = {i.id for i in load_demo()}
    subsets = {
        "все": scores,
        "демо": [s for s in scores if s.qid in demo_ids],
        "доп.": [s for s in scores if s.qid not in demo_ids],
    }
    lines = [
        f"# Answers eval {args.label}".rstrip(),
        "",
        f"judge: `{JUDGE_VERSION}/{args.judge}`, team: `{args.team}`, baseline: `{args.baseline}`",
        "",
        "| набор | n | Score | MatchRate | WinRate | W/L/T |",
        "|---|---|---|---|---|---|",
        *(_row(name, leaderboard(part)) for name, part in subsets.items() if part),
        "",
        f"avg words: {sum(len(team[i.id].split()) for i in items) / max(len(items), 1):.0f}",
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


def _row(name: str, board: Leaderboard) -> str:
    return (
        f"| {name} | {board.n} | {board.score:.3f} | {board.match_rate:.3f} | "
        f"{board.win_rate:.3f} | {board.wins}/{board.losses}/{board.ties} |"
    )


if __name__ == "__main__":
    sys.exit(main())
