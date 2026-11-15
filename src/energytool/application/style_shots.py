"""Примеры для промпта из образцов ответов: «вопрос → найденные разделы → ответ образца».

    prepare_style_shots(examples, plan_for, embedder) -> StyleShotSelector
    StyleShotSelector.select(cell, k) -> tuple[StyleShot, ...]

Образец сам по себе — только вопрос и ответ. Чтобы пример в промпте выглядел так же, как
текущий запрос, для вопроса образца один раз на старте выполняется тот же поиск, что и для
вопросов клиента. Для каждого вопроса клиента выбираются k ближайших образцов по близости
эмбеддингов: модель видит, как на похожий вопрос отвечал консультант — какую оговорку сделал
и что уточнил.

Эмбеддер недоступен — примеры не выбираются (остаются встроенные в промпт), ответ не падает.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from energytool.application.ports import Embedder, ExternalServiceError
from energytool.application.transcript import parse_transcript
from energytool.domain.reply import ReplyPlan, StyleExample, StyleShot

logger = logging.getLogger(__name__)

PlanFor = Callable[[str], ReplyPlan]


@dataclass(frozen=True, slots=True)
class StyleShotSelector:
    shots: tuple[StyleShot, ...]
    vectors: tuple[tuple[float, ...], ...]
    embedder: Embedder
    holdout: bool = False  # eval: не показывать образец с тем же вопросом

    def select(self, cell: str, k: int) -> tuple[StyleShot, ...]:
        if not self.shots or k <= 0:
            return ()
        try:
            (query,) = self.embedder.embed([_squash(cell)])
        except ExternalServiceError as exc:
            logger.warning("style shots skipped, embedder failed: %s", exc)
            return ()
        ranked = sorted(
            zip(self.shots, self.vectors, strict=True),
            key=lambda pair: _cosine(query, pair[1]),
            reverse=True,
        )
        same = _squash(cell)
        chosen = [s for s, _ in ranked if not (self.holdout and _squash(s.question) == same)][:k]
        # ближайший образец — последним: рядом с вопросом клиента
        return tuple(reversed(chosen))


def prepare_style_shots(
    examples: Sequence[StyleExample],
    plan_for: PlanFor,
    embedder: Embedder,
    *,
    holdout: bool = False,
) -> StyleShotSelector:
    empty = StyleShotSelector((), (), embedder, holdout)
    if not examples:
        return empty
    try:
        vectors = embedder.embed([_squash(e.question) for e in examples])
        shots = tuple(_shot(e, plan_for) for e in examples)
    except ExternalServiceError as exc:
        logger.warning("style shots disabled: %s", exc)
        return empty
    logger.info("style shots prepared", extra={"data": {"count": len(shots)}})
    return StyleShotSelector(shots, tuple(tuple(v) for v in vectors), embedder, holdout)


def _shot(example: StyleExample, plan_for: PlanFor) -> StyleShot:
    return StyleShot(
        question=example.question,
        history=tuple(parse_transcript(example.question)),
        evidence=plan_for(example.question).evidence,
        answer=example.answer,
    )


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()
