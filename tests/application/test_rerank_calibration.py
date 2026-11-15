from collections.abc import Sequence

from energytool.application.ports import ExternalServiceError
from energytool.application.rerank_calibration import rerank_scores_calibrated
from energytool.domain.knowledge import Chunk, SectionKind, Topic
from energytool.domain.products import Product
from tests.application.fakes import chunk


def corpus() -> list[Chunk]:
    faq = [
        Chunk(
            id=f"faq{i}.md#q",
            file=f"faq{i}.md",
            path=f"Частые вопросы › Вопрос {i}",
            text=f"Частые вопросы › Вопрос {i}\nответ {i}",
            scope=Topic.ENERGY,
            kind=SectionKind.FAQ,
        )
        for i in range(3)
    ]
    return [*faq, *(chunk(f"other{i}.md#x", f"текст {i}", Product.OREM) for i in range(6))]


class ScaledReranker:
    """Свой раздел (первый документ) получает best, остальные — rest."""

    def __init__(self, best: float, rest: float) -> None:
        self._best, self._rest = best, rest

    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        return [(0, self._best), *((i, self._rest) for i in range(1, len(documents)))]


class BrokenReranker:
    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        raise ExternalServiceError("нет связи")


def test_probability_scores_are_calibrated() -> None:
    assert rerank_scores_calibrated(ScaledReranker(0.97, 0.01), corpus()) is True


def test_logit_or_tiny_scores_switch_thresholds_off() -> None:
    assert rerank_scores_calibrated(ScaledReranker(7.5, -3.0), corpus()) is False
    assert rerank_scores_calibrated(ScaledReranker(0.04, 0.001), corpus()) is False


def test_failure_or_small_base_is_not_calibrated() -> None:
    assert rerank_scores_calibrated(BrokenReranker(), corpus()) is False
    assert rerank_scores_calibrated(ScaledReranker(0.97, 0.01), corpus()[:2]) is False
