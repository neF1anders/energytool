"""Проверка шкалы реранкера на старте батча.

    rerank_scores_calibrated(reranker, chunks) -> bool

Отсечение слабых разделов и признак «в базе нет ответа» опираются на абсолютные пороги
скора (вероятность 0..1, как у Qwen3-Reranker на OpenRouter). Прокси платформы может
отдавать скоры в другой шкале (логиты, ненормированные значения) — тогда пороги отрезали бы
нужные разделы или превратили бы все ответы в отказы. Поэтому перед прогоном несколько
разделов-вопросов базы ранжируются по собственному заголовку: если свой раздел не получает
высокий скор в пределах 0..1, пороги выключаются и остаётся обычный top-k.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from energytool.application.build_index import PATH_SEPARATOR
from energytool.application.ports import ExternalServiceError, Reranker
from energytool.domain.knowledge import Chunk, SectionKind

logger = logging.getLogger(__name__)

PROBES = 3
DISTRACTORS = 3
MIN_OWN_SCORE = 0.5
MIN_PASSED = 2


def rerank_scores_calibrated(reranker: Reranker, chunks: Sequence[Chunk]) -> bool:
    questions = [c for c in chunks if c.kind is SectionKind.FAQ and PATH_SEPARATOR in c.path]
    if len(questions) < PROBES or len(chunks) <= DISTRACTORS:
        return False
    step = len(questions) // PROBES
    probes = [questions[i * step] for i in range(PROBES)]
    passed = 0
    observed: list[list[float]] = []
    for number, probe in enumerate(probes):
        others = [c for c in chunks if c.file != probe.file]
        spread = max(len(others) // DISTRACTORS, 1)
        distractors = others[number::spread][:DISTRACTORS]
        documents = [probe.text, *(c.text for c in distractors)]
        query = probe.path.rsplit(PATH_SEPARATOR, 1)[-1]
        try:
            ranked = reranker.rerank(query, documents, len(documents))
        except ExternalServiceError as exc:
            logger.warning("reranker calibration failed: %s", exc)
            return False
        scores = dict(ranked)
        observed.append([round(scores.get(i, -1.0), 3) for i in range(len(documents))])
        in_range = all(0.0 <= score <= 1.0 for score in scores.values())
        own_is_best = bool(ranked) and ranked[0][0] == 0 and ranked[0][1] >= MIN_OWN_SCORE
        passed += in_range and own_is_best
    calibrated = passed >= MIN_PASSED
    logger.info(
        "reranker calibrated" if calibrated else "reranker scale unknown, score thresholds off",
        extra={"data": {"passed": passed, "probes": PROBES, "scores": observed}},
    )
    return calibrated
