"""Сквозной сценарий веб-чата на скриптовом Extractor: хлебозавод от боли до заявки."""

from collections.abc import Sequence

from energytool.application.handle_turn import HandleTurn
from energytool.application.ports import ExternalServiceError
from energytool.application.qualification import Qualifier, StepResult
from energytool.application.trace import TurnTrace
from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Extraction, Intent, SlotUpdate
from energytool.domain.knowledge import BudgetSnippet, KnowledgeQuery, KnowledgeResult
from energytool.domain.policy import Thresholds
from energytool.domain.products import Product
from energytool.domain.reply import Reply, ReplyPlan
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scoring import ScoringParams
from energytool.domain.slots import SlotSpec
from energytool.domain.state import ConversationState, DialogState, Message


class MemorySessions:
    def __init__(self) -> None:
        self.states: dict[str, ConversationState] = {}
        self.traces: list[TurnTrace] = []

    def load(self, session_id: str) -> ConversationState | None:
        return self.states.get(session_id)

    def save(self, state: ConversationState, trace: TurnTrace) -> None:
        self.states[state.session_id] = state
        self.traces.append(trace)


class ScriptedExtractor:
    def __init__(self, *script: Extraction | Exception) -> None:
        self.script = list(script)

    def extract(
        self, history: Sequence[Message], state: ConversationState, catalog: Sequence[SlotSpec]
    ) -> Extraction:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class PlanWriter:
    def __init__(self) -> None:
        self.plans: list[ReplyPlan] = []

    def write(self, plan: ReplyPlan, history: Sequence[Message]) -> Reply:
        self.plans.append(plan)
        ask = plan.ask.slot.id if plan.ask else "-"
        return Reply(
            text=f"ask={ask} cta={plan.offer_contact}", used_llm=bool(plan.answer_question)
        )


class NullTelemetry:
    def record(self, trace: TurnTrace) -> None:
        pass


def no_knowledge(query: KnowledgeQuery) -> KnowledgeResult:
    return KnowledgeResult(evidence=())


def u(slot: str, value: object, quote: str) -> SlotUpdate:
    return SlotUpdate(slot, value, quote)  # type: ignore[arg-type]


def make(
    extractor: ScriptedExtractor, leads: list[StepResult]
) -> tuple[HandleTurn, MemorySessions, PlanWriter]:
    sessions, writer = MemorySessions(), PlanWriter()
    qualifier = Qualifier(
        ContractRegistry(),
        RULES,
        PRIORS,
        ScoringParams(),
        Thresholds(),
        no_knowledge,
        BudgetSnippet(),
    )

    def submit(step: StepResult) -> str:
        leads.append(step)
        return "lead-1"

    return (
        HandleTurn(sessions, extractor, qualifier, writer, NullTelemetry(), submit_lead=submit),
        sessions,
        writer,
    )


def test_bakery_from_pain_to_lead() -> None:
    leads: list[StepResult] = []
    script = ScriptedExtractor(
        Extraction(
            Intent.MIXED,
            slot_updates=(
                u("segment", "industry", "хлебозавод"),
                u("goal", "reduce_bill", "снизить счета"),
            ),
            search_query="как снизить счета за электроэнергию",
        ),
        Extraction(Intent.INFO, slot_updates=(u("max_power_kw", 1.1, "1,1 МВт"),)),
        Extraction(
            Intent.INFO,
            slot_updates=(
                u("monthly_consumption_kwh", "1,2 млн", "1,2 млн кВт·ч"),
                u("region", "Тула", "в Туле"),
                u("current_supplier", "Тулаэнергосбыт", "Тулаэнергосбыт"),
            ),
        ),
        Extraction(
            Intent.CONTACT,
            wants_contact=True,
            slot_updates=(
                u("contact_name", "Иван Петров", "Иван Петров"),
                u("phone", "+7 912 345-67-89", "+7 912 345-67-89"),
                u("company", "Хлебозавод №1", "Хлебозавод №1"),
                u("pd_consent", True, "согласен"),
            ),
        ),
    )
    turn, sessions, writer = make(script, leads)
    r1 = turn("s1", "Мы хлебозавод, хотим снизить счета")
    assert r1.dialog is DialogState.QUALIFY
    assert r1.trace.dialog["leader"] == "orem"
    assert writer.plans[-1].ask is not None and writer.plans[-1].ask.slot.id == "max_power_kw"

    turn("s1", "1,1 МВт")
    assert writer.plans[-1].answer_question is None  # ответ на анкету → шаблон без LLM
    assert any("1 100 кВт" in a for a in writer.plans[-1].acknowledge)

    r3 = turn("s1", "1,2 млн кВт·ч в месяц, мы в Туле, сбыт — Тулаэнергосбыт")
    assert r3.dialog is DialogState.READY
    assert writer.plans[-1].offer_contact is True

    r4 = turn("s1", "Иван Петров, +7 912 345-67-89, Хлебозавод №1, согласен")
    assert r4.dialog is DialogState.SUBMITTED
    assert r4.lead_id == "lead-1"
    assert leads[0].board.ranked()[0].product is Product.OREM

    state = sessions.states["s1"]
    assert state.turn == 4 and len(state.history) == 8
    assert [t.turn for t in sessions.traces] == [1, 2, 3, 4]


def test_extractor_failure_keeps_state_and_answers() -> None:
    turn, sessions, writer = make(ScriptedExtractor(ExternalServiceError("down")), [])
    response = turn("s2", "Что такое ГТП?")
    assert response.reply.text
    assert "extractor_failed" in response.trace.degraded
    assert writer.plans[-1].answer_question == "Что такое ГТП?"
    assert sessions.states["s2"].slots == {}
