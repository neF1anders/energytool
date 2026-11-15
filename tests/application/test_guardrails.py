from collections.abc import Sequence

from energytool.application.answer_question import AnswerQuestion
from energytool.application.guardrails import (
    GENERAL_GUARD_IDS,
    GENERAL_HINT_ID,
    PRODUCT_LIMIT_IDS,
    guard_query,
)
from energytool.application.trace import TurnTrace
from energytool.domain.knowledge import (
    Evidence,
    IdSnippet,
    KnowledgeQuery,
    KnowledgeResult,
    PinSnippet,
    TextSnippet,
    Topic,
)
from energytool.domain.products import Product
from energytool.domain.reply import Reply, ReplyPlan
from energytool.domain.state import Message
from tests.application.fakes import chunk


def test_guard_query_takes_limits_of_dominant_products_only() -> None:
    query = guard_query({Product.SOLAR_GRID: 0.7, Product.OREM: 0.2, Topic.ENERGY: 0.1})
    (ids,) = [s.ids for s in query if isinstance(s, IdSnippet)]
    assert ids == (*GENERAL_GUARD_IDS, PRODUCT_LIMIT_IDS[Product.SOLAR_GRID])
    assert [s.scope for s in query if isinstance(s, PinSnippet)] == [Product.SOLAR_GRID]
    assert not any(isinstance(s, TextSnippet) for s in query)


def test_guard_query_without_product_adds_general_hint() -> None:
    (ids,) = [s.ids for s in guard_query({Topic.ENERGY: 1.0}) if isinstance(s, IdSnippet)]
    assert ids == (*GENERAL_GUARD_IDS, GENERAL_HINT_ID)


class _Writer:
    def __init__(self) -> None:
        self.plans: list[ReplyPlan] = []

    def write(self, plan: ReplyPlan, history: Sequence[Message]) -> Reply:
        self.plans.append(plan)
        return Reply(text="ответ", used_llm=True)


class _Traces:
    def record(self, trace: TurnTrace) -> None:
        pass


def test_answer_adds_guard_sections_as_pinned_and_hint_without_duplicates() -> None:
    found = Evidence(chunk("solar#кровля", "кровля", Product.SOLAR_GRID), 0.9, "ranked")
    limit = Evidence(chunk("limits#солнце", "нельзя", Product.SOLAR_GRID), 1.0, "by_id")
    hint = Evidence(chunk(GENERAL_HINT_ID, "регион", Topic.GENERAL), 1.0, "by_id")

    def retrieve(query: KnowledgeQuery) -> KnowledgeResult:
        if any(isinstance(s, TextSnippet) for s in query):
            return KnowledgeResult(
                evidence=(found,), below_floor=True, scope_mass={Product.SOLAR_GRID: 1.0}
            )
        return KnowledgeResult(evidence=(found, limit, hint))

    writer = _Writer()
    AnswerQuestion(retrieve, writer, _Traces())("1", "Панели на шифер ставите?")
    (plan,) = writer.plans
    assert [(e.chunk.id, e.origin) for e in plan.evidence] == [
        ("solar#кровля", "ranked"),
        ("limits#солнце", "pinned"),
        (GENERAL_HINT_ID, "hint"),
    ]
    assert plan.weak_evidence is True


def test_answer_without_guardrails_keeps_only_found_sections() -> None:
    def retrieve(query: KnowledgeQuery) -> KnowledgeResult:
        return KnowledgeResult(evidence=())

    writer = _Writer()
    AnswerQuestion(retrieve, writer, _Traces(), with_guardrails=False)("1", "Вопрос?")
    assert writer.plans[0].evidence == ()
