"""Точка входа этапа 1 (solution.py): questions.csv → submission.csv.

    DATA_DIR/questions.csv + DATA_DIR/knowledge-base.zip → ./submission.csv

- вопросы обрабатываются параллельно по MAX_WORKERS;
- бюджет времени: после RUN_BUDGET_SEC (по умолчанию 18 мин из 20) — ответы без LLM;
- любая ошибка кейса → запасной ответ, прогон не падает и всегда пишет CSV;
- итог прогона (время, p50/p95, деградации) — в лог и в stdout.
"""

from __future__ import annotations

import logging
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from energytool.application.answer_question import AnswerMode, AnswerQuestion
from energytool.bootstrap import Container, build
from energytool.domain.knowledge import BudgetSnippet
from energytool.infrastructure.logs import log_context
from energytool.interface.batch.submission import (
    EMPTY_ANSWER,
    Question,
    find_questions,
    read_questions,
    write_submission,
)

logger = logging.getLogger(__name__)

OUTPUT = Path("submission.csv")
PROGRESS_EVERY = 10


class Deadline:
    """Режим по прошедшему времени: FULL → FAST (без Extractor) → NO_LLM (spec §6.3)."""

    def __init__(self, fast_after_sec: float, no_llm_after_sec: float) -> None:
        self._started = time.monotonic()
        self._fast_after = fast_after_sec
        self._no_llm_after = no_llm_after_sec

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def mode(self) -> AnswerMode:
        elapsed = self.elapsed()
        if elapsed >= self._no_llm_after:
            return AnswerMode.NO_LLM
        return AnswerMode.FULL if elapsed < self._fast_after else AnswerMode.FAST


def main() -> int:
    container = build()
    deadline = Deadline(container.settings.fast_path_after_sec, container.settings.run_budget_sec)
    roots = [*container.data_roots(), Path(sys.argv[0]).resolve().parent]
    questions = read_questions(find_questions(roots))
    logger.info("questions loaded", extra={"data": {"count": len(questions)}})
    print(f"Вопросов: {len(questions)}", flush=True)
    answers: dict[str, str] = {}
    try:
        answers = answer_all(container, questions, deadline)
    except Exception:
        logger.exception("batch failed before all answers; writing what we have")
    finally:
        rows = [(q.id, answers.get(q.id, EMPTY_ANSWER)) for q in questions]
        written = write_submission(OUTPUT, rows)
        print(f"Готов {OUTPUT}: {written} ответов за {deadline.elapsed():.0f} с", flush=True)
        container.close()
    return 0


def answer_all(
    container: Container, questions: list[Question], deadline: Deadline
) -> dict[str, str]:
    container.gateway.calibrate_thinking()
    index = container.build_index()
    settings = container.settings
    retrieve = container.retriever(index)
    budget = BudgetSnippet(top_k=settings.top_k, fallback_k=1)
    full = settings.batch_pipeline == "full"
    use_case = AnswerQuestion(
        retrieve=retrieve,
        writer=container.reply_writer(),
        telemetry=container.telemetry,
        budget=budget,
        extractor=container.extractor() if full else None,
        qualifier=container.qualifier(retrieve, budget) if full else None,
        ask_in_batch=settings.batch_ask,
    )
    answers: dict[str, str] = {}
    durations: list[float] = []
    modes: dict[str, int] = {}

    def work(question: Question) -> tuple[str, str, float, AnswerMode]:
        started = time.perf_counter()
        mode = deadline.mode()
        with log_context(qid=question.id):
            try:
                text = use_case(question.id, question.text, mode).text
            except Exception:
                logger.exception("case failed, fallback answer")
                text = EMPTY_ANSWER
        return question.id, text, time.perf_counter() - started, mode

    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        futures = [pool.submit(work, q) for q in questions]
        for done, future in enumerate(as_completed(futures), 1):
            qid, text, seconds, mode = future.result()
            answers[qid] = text
            durations.append(seconds)
            modes[mode] = modes.get(mode, 0) + 1
            if done % PROGRESS_EVERY == 0 or done == len(questions):
                print(f"  {done}/{len(questions)} за {deadline.elapsed():.0f} с", flush=True)
    log_summary(durations, modes, deadline, answers)
    return answers


def log_summary(
    durations: list[float], modes: dict[str, int], deadline: Deadline, answers: dict[str, str]
) -> None:
    ordered = sorted(durations)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0.0
    summary = {
        "answers": len(answers),
        "elapsed_sec": round(deadline.elapsed(), 1),
        "p50_sec": round(statistics.median(ordered), 2) if ordered else 0.0,
        "p95_sec": round(p95, 2),
        "modes": modes,
        "fallback_answers": sum(1 for a in answers.values() if a == EMPTY_ANSWER),
    }
    logger.info("batch summary", extra={"data": summary})
    print(f"Итог: {summary}", flush=True)
