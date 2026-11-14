from collections.abc import Sequence

from energytool.application.answer_question import AnswerMode, AnswerQuestion
from energytool.application.ports import ExternalServiceError
from energytool.application.trace import TurnTrace
from energytool.domain.knowledge import KnowledgeQuery, KnowledgeResult, TextSnippet
from energytool.domain.reply import Reply, ReplyPlan
from energytool.domain.state import Message


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
