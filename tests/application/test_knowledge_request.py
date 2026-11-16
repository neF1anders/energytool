from energytool.application.knowledge_request import build_query
from energytool.domain.extraction import Extraction, Intent
from energytool.domain.knowledge import (
    BoostSnippet,
    BudgetSnippet,
    IdSnippet,
    PinSnippet,
    ScopeSnippet,
    TextSnippet,
    Topic,
)
from energytool.domain.policy import PolicyDecision
from energytool.domain.products import Product
from energytool.domain.state import ConversationState, DialogState, Message, Role

STATE = ConversationState("s", history=[Message(Role.USER, "А на фасад можно?", 1)])
BUDGET = BudgetSnippet(top_k=3)


def decision(
    need_facts: bool = True,
    leader: Product | None = Product.SOLAR_HYBRID,
    pinned: tuple[str, ...] = (),
) -> PolicyDecision:
    return PolicyDecision(
        DialogState.QUALIFY,
        DialogState.QUALIFY,
        candidates=(Product.SOLAR_HYBRID, Product.METERING),
        leader=leader,
        need_facts=need_facts,
        next_slot=None,
        offer_contact=False,
        pinned_sections=pinned,
    )


def test_full_query_by_spec() -> None:
    ex = Extraction(Intent.QUESTION, search_query="солнечные панели на фасаде здания")
    query = build_query(ex, STATE, decision(pinned=("a#b",)), BUDGET)
    assert query[0] == IdSnippet(("a#b",))
    assert TextSnippet("солнечные панели на фасаде здания", 1.0) in query
    assert TextSnippet("А на фасад можно?", 0.5) in query
    scope = next(s for s in query if isinstance(s, ScopeSnippet))
    assert scope.scopes == frozenset(
        {Product.SOLAR_GRID, Product.METERING, Topic.GENERAL, Topic.ENERGY}
    )
    assert BoostSnippet(Product.SOLAR_GRID, 1.2) in query
    assert any(isinstance(s, PinSnippet) and s.scope is Product.SOLAR_GRID for s in query)
    assert query[-1] == BUDGET


def test_no_facts_means_only_ids_and_budget() -> None:
    query = build_query(
        Extraction(Intent.INFO), STATE, decision(need_facts=False, pinned=("x",)), BUDGET
    )
    assert query == (IdSnippet(("x",)), BUDGET)


def test_batch_does_not_restrict_scope_and_falls_back_to_last_message() -> None:
    query = build_query(
        Extraction(Intent.QUESTION), STATE, decision(leader=None), BUDGET, restrict_scope=False
    )
    assert not any(isinstance(s, ScopeSnippet | BoostSnippet | PinSnippet) for s in query)
    assert query[0] == TextSnippet("А на фасад можно?", 1.0)


def test_history_context_adds_earlier_client_turns_and_last_consultant_reply() -> None:
    state = ConversationState(
        "s",
        history=[
            Message(Role.USER, "У нас склад в Подмосковье", 1),
            Message(Role.ASSISTANT, "Какая цель?", 1),
            Message(Role.USER, "Снизить платёж. Есть смысл?", 2),
        ],
    )
    ex = Extraction(Intent.QUESTION, search_query="окупаемость солнечной станции")
    plain = build_query(ex, state, decision(), BUDGET, restrict_scope=False)
    rich = build_query(ex, state, decision(), BUDGET, restrict_scope=False, history_context=True)
    assert TextSnippet("У нас склад в Подмосковье", 0.5) not in plain
    assert TextSnippet("У нас склад в Подмосковье", 0.5) in rich
    assert TextSnippet("Какая цель?", role="context") in rich


def test_history_context_is_empty_for_single_question() -> None:
    ex = Extraction(Intent.QUESTION, search_query="панели на фасаде")
    plain = build_query(ex, STATE, decision(), BUDGET)
    assert build_query(ex, STATE, decision(), BUDGET, history_context=True) == plain
