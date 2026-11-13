"""Стенд ретрива: `uv run --env-file .env python -m eval.retrieval [--configs ...] [--sets ...]`.

Строит индекс по materials/data/knowledge-base.zip, прогоняет наборы через
RetrieveKnowledge с разными PipelineConfig и печатает таблицу; полный отчёт с промахами
сохраняется в eval/data/results/.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence

from energytool.application.build_index import KnowledgeIndex
from energytool.application.retrieval_stages import CONFIGS
from energytool.bootstrap import Container, build
from energytool.domain.knowledge import BudgetSnippet
from eval.common import KNOWLEDGE_BASE, results_path
from eval.retrieval.datasets import Case, demo_cases, faq_cases, paraphrase_cases
from eval.retrieval.metrics import CaseResult, Summary, summarize

logger = logging.getLogger("eval.retrieval")

BUDGET = BudgetSnippet(top_k=10, fallback_k=0, max_tokens=100_000)
SETS = ("faq", "paraphrase", "demo")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.retrieval")
    parser.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    parser.add_argument("--sets", nargs="+", default=list(SETS), choices=SETS)
    args = parser.parse_args(argv)

    container = build()
    index = container.build_index(KNOWLEDGE_BASE)
    cases = load_cases(container, index, args.sets)
    rows: list[tuple[str, str, Summary]] = []
    misses: list[str] = []
    for config in args.configs:
        retrieve = container.retriever(index, config)
        for set_name in args.sets:
            subset = [c for c in cases if c.set == set_name]
            results = [run_case(retrieve, index, case) for case in subset]
            rows.append((config, set_name, summarize(results)))
            misses += [
                format_miss(config, case, r)
                for case, r in zip(subset, results, strict=True)
                if r.rank() != 1
            ]
    report = render(rows, misses)
    print(report.split("\n## Промахи")[0])
    path = results_path("retrieval")
    path.write_text(report, encoding="utf-8")
    print(f"отчёт: {path}")
    container.close()
    return 0


def load_cases(container: Container, index: KnowledgeIndex, sets: Sequence[str]) -> list[Case]:
    chunks = index.chunks.all()
    cases: list[Case] = []
    if "faq" in sets:
        cases += faq_cases(chunks, BUDGET)
    if "paraphrase" in sets:
        cases += paraphrase_cases(chunks, BUDGET, container.gateway)
    if "demo" in sets:
        cases += demo_cases(BUDGET)
    logger.info("cases loaded", extra={"data": {s: sum(c.set == s for c in cases) for s in sets}})
    return cases


def run_case(retrieve, index: KnowledgeIndex, case: Case) -> CaseResult:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    result = retrieve(case.query)
    ms = (time.perf_counter() - started) * 1000
    ranked = tuple(e.chunk.id for e in result.evidence if e.origin == "ranked")
    expected_scopes = frozenset(str(c.scope) for c in index.chunks.get(case.expected))
    top_scope = str(result.evidence[0].chunk.scope) if result.evidence else None
    return CaseResult(case.id, ranked, case.expected, expected_scopes, top_scope, ms)


def format_miss(config: str, case: Case, result: CaseResult) -> str:
    return (
        f"- `{config}` [{case.set}] rank={result.rank()} «{case.text[:90]}»\n"
        f"  ждали: {', '.join(sorted(case.expected))}\n"
        f"  top3: {', '.join(result.ranked[:3])}"
    )


def render(rows: list[tuple[str, str, Summary]], misses: list[str]) -> str:
    lines = [
        "# Retrieval eval",
        "",
        "| config | set | n | hit@1 | recall@4 | MRR@10 | scope@1 | p50 ms | max ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for config, set_name, s in rows:
        lines.append(
            f"| {config} | {set_name} | {s.n} | {s.hit_at_1:.3f} | {s.recall_at_4:.3f} | "
            f"{s.mrr_at_10:.3f} | {s.scope_at_1:.3f} | {s.p50_ms:.0f} | {s.max_ms:.0f} |"
        )
    lines += ["", "## Промахи (rank ≠ 1)", "", *misses, ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
