from collections.abc import Sequence

from energytool.application.answer_question import AnswerMode, AnswerQuestion
from energytool.application.ports import ExternalServiceError
from energytool.application.qualification import Qualifier
from energytool.application.trace import TurnTrace
from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Extraction, Intent
from energytool.domain.knowledge import (
    BudgetSnippet,
    Evidence,
    IdSnippet,
    KnowledgeQuery,
    KnowledgeResult,
    TextSnippet,
    Topic,
)
from energytool.domain.policy import Thresholds
from energytool.domain.reply import Reply, ReplyPlan
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scoring import ScoringParams
from energytool.domain.state import ConversationState, Message
from tests.application.fakes import chunk


class RecordingWriter:
    def __init__(self) -> None:
        self.plans: list[ReplyPlan] = []
        self.histories: list[Sequence[Message]] = []

    def write(self, plan: ReplyPlan, history: Sequence[Message]) -> Reply:
        self.plans.append(plan)
        self.histories.append(history)
        return Reply(text="ответ", used_llm=not plan.template_only)


class Traces:
    def __init__(self) -> None:
        self.items: list[TurnTrace] = []

    def record(self, trace: TurnTrace) -> None:
        self.items.append(trace)


def test_dialog_cell_answers_last_user_message_with_history_query() -> None:
    queries: list[KnowledgeQuery] = []

    def retrieve(query: KnowledgeQuery) -> KnowledgeResult:
        queries.append(query)
        return KnowledgeResult(evidence=(), trace={"notes": ["dense_failed"]})

    writer, traces = RecordingWriter(), Traces()
    use_case = AnswerQuestion(retrieve, writer, traces)
    reply = use_case("7", "Пользователь: Мы завод\nАссистент: Мощность?\nПользователь: 1 МВт. Опт?")
    assert reply.text == "ответ"
    assert writer.plans[0].answer_question == "1 МВт. Опт?"
    assert len(writer.histories[0]) == 3
    texts = [s.text for s in queries[0] if isinstance(s, TextSnippet)]
    assert texts[0] == "1 МВт. Опт?"
    (trace,) = traces.items
    assert trace.session_id == "7"
    assert trace.degraded == ["dense_failed"]
    assert trace.reply_words == 1
    assert {"retrieve", "respond", "total"} <= set(trace.timings_ms)


def test_retrieval_failure_still_answers_and_no_llm_mode_is_template() -> None:
    def broken(query: KnowledgeQuery) -> KnowledgeResult:
        raise ExternalServiceError("down")

    writer, traces = RecordingWriter(), Traces()
    reply = AnswerQuestion(broken, writer, traces)("1", "Вопрос?", AnswerMode.NO_LLM)
    assert reply.text == "ответ"
    assert writer.plans[0].evidence == ()
    assert writer.plans[0].template_only is True
    assert traces.items[0].degraded == ["retrieval_failed", "template_reply"]


class FixedCondenser:
    def condense(self, history: Sequence[Message]) -> str:
        return "Завод 1 МВт: подходит ли оптовый рынок?"


def test_dialog_search_uses_condensed_question_and_single_question_does_not() -> None:
    queries: list[KnowledgeQuery] = []

    def retrieve(query: KnowledgeQuery) -> KnowledgeResult:
        queries.append(query)
        return KnowledgeResult(evidence=())

    use_case = AnswerQuestion(
        retrieve, RecordingWriter(), Traces(), condenser=FixedCondenser(), with_guardrails=False
    )
    use_case("1", "Пользователь: Мы завод\nАссистент: Мощность?\nПользователь: 1 МВт. Опт?")
    use_case("2", "Подходит ли нам опт?")
    dialog, single = ([s.text for s in q if isinstance(s, TextSnippet)] for q in queries)
    assert dialog == ["Завод 1 МВт: подходит ли оптовый рынок?", "1 МВт. Опт?"]
    assert single == ["Подходит ли нам опт?"]


GUARD = chunk("limits#общие-границы", "точную стоимость не называем", Topic.GENERAL)
FOUND = chunk("orem#порог", "порог оптового рынка 750 кВт", Topic.ENERGY)


class CountingExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract(
        self, history: Sequence[Message], state: ConversationState, catalog: object
    ) -> Extraction:
        self.calls += 1
        return Extraction(Intent.QUESTION, search_query="оптовый рынок порог мощности")


def full_use_case(
    extractor: CountingExtractor, *, dialogs_only: bool
) -> tuple[AnswerQuestion, RecordingWriter]:
    def retrieve(query: KnowledgeQuery) -> KnowledgeResult:
        if any(isinstance(s, IdSnippet) for s in query):  # запрос разделов-ограничений
            return KnowledgeResult(evidence=(Evidence(GUARD, 1.0, "pinned"),))
        return KnowledgeResult(evidence=(Evidence(FOUND, 0.05, "ranked"),), below_floor=True)

    qualifier = Qualifier(
        ContractRegistry(), RULES, PRIORS, ScoringParams(), Thresholds(), retrieve, BudgetSnippet()
    )
    writer = RecordingWriter()
    use_case = AnswerQuestion(
        retrieve,
        writer,
        Traces(),
        extractor=extractor,
        qualifier=qualifier,
        full_for_dialogs_only=dialogs_only,
    )
    return use_case, writer


def test_full_plan_gets_guardrails_and_weak_evidence_like_naive_path() -> None:
    extractor = CountingExtractor()
    use_case, writer = full_use_case(extractor, dialogs_only=False)
    use_case("1", "Пользователь: Мы завод на 1 МВт\nАссистент: Цель?\nПользователь: Опт подходит?")
    (plan,) = writer.plans
    assert extractor.calls == 1
    assert [e.chunk.id for e in plan.evidence] == [FOUND.id, GUARD.id]
    assert plan.weak_evidence is True
    assert plan.acknowledge == ()


def test_dialogs_only_pipeline_sends_single_questions_by_naive_path() -> None:
    extractor = CountingExtractor()
    use_case, _ = full_use_case(extractor, dialogs_only=True)
    use_case("1", "Подходит ли нам опт?")
    assert extractor.calls == 0
    use_case("2", "Пользователь: Мы завод\nАссистент: Мощность?\nПользователь: 1 МВт. Опт?")
    assert extractor.calls == 1
