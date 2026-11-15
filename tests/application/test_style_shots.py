from collections.abc import Sequence

from energytool.application.ports import ExternalServiceError
from energytool.application.style_shots import prepare_style_shots
from energytool.domain.knowledge import Evidence
from energytool.domain.products import Product
from energytool.domain.reply import ReplyPlan, StyleExample
from tests.application.fakes import chunk

EXAMPLES = (
    StyleExample("Панели на фасад ставите?", "Да, ставим."),
    StyleExample("Сколько стоит учёт?", "Считаем под задачу."),
    StyleExample(
        "Пользователь: У нас завод.\nАссистент: Понял.\nПользователь: Что проверить?",
        "Сравним счета.",
    ),
)
VECTORS = {"фасад": [1.0, 0.0], "учёт": [0.0, 1.0], "завод": [0.7, 0.7]}


class _Embedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [next(v for word, v in VECTORS.items() if word in text) for text in texts]


class _BrokenEmbedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise ExternalServiceError("embedder down")


def _plan_for(cell: str) -> ReplyPlan:
    found = Evidence(chunk("solar#фасад", cell, Product.SOLAR_GRID), 0.9, "ranked")
    return ReplyPlan(answer_question=cell, evidence=(found,), ask=None, offer_contact=False)


def test_nearest_examples_come_with_their_evidence_closest_last() -> None:
    selector = prepare_style_shots(EXAMPLES, _plan_for, _Embedder())
    shots = selector.select("А на фасад можно?", k=2)
    assert [s.answer for s in shots] == ["Сравним счета.", "Да, ставим."]
    assert shots[-1].evidence[0].chunk.text == "Панели на фасад ставите?"
    assert [m.text for m in shots[0].history] == ["У нас завод.", "Понял.", "Что проверить?"]


def test_holdout_hides_example_with_the_same_question() -> None:
    selector = prepare_style_shots(EXAMPLES, _plan_for, _Embedder(), holdout=True)
    assert [s.answer for s in selector.select("панели на  фасад ставите?", k=1)] == [
        "Сравним счета."
    ]


def test_broken_embedder_gives_no_shots_instead_of_failure() -> None:
    assert prepare_style_shots(EXAMPLES, _plan_for, _BrokenEmbedder()).select("фасад", 2) == ()
    assert prepare_style_shots((), _plan_for, _Embedder()).select("фасад", 2) == ()
