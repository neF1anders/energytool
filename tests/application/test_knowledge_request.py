from energytool.application.knowledge_request import build_query
from energytool.application.retrieval_stages import get_config, parse_query
from energytool.application.retrieve_knowledge import RetrieveKnowledge
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
from energytool.domain.slots import SlotStatus, SlotValue
from energytool.domain.state import ConversationState, DialogState, Message, Role
from tests.application.fakes import CORPUS, chunk, toy_index

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


def test_id14_explicit_solar_mention_routes_retrieval_over_orem_leader() -> None:
    state = ConversationState(
        "id14",
        history=[
            Message(Role.USER, "Хотим поставить солнечную станцию.", 1),
            Message(Role.ASSISTANT, "Что важнее: расходы или резерв?", 2),
            Message(Role.USER, "И то и другое, но больше всего тратим вечером.", 3),
            Message(Role.USER, "Обычная сетевая станция вообще имеет смысл?", 4),
        ],
    )
    wrong_orem_leader = PolicyDecision(
        DialogState.OPEN,
        DialogState.QUALIFY,
        candidates=(Product.OREM,),
        leader=Product.OREM,
        need_facts=True,
        next_slot="max_power_kw",
        offer_contact=False,
    )
    extraction = Extraction(
        Intent.QUESTION,
        mentions=(Product.SOLAR_GRID,),
        search_query="эффективность сетевой солнечной станции при вечернем потреблении",
    )

    query = build_query(extraction, state, wrong_orem_leader, BUDGET)

    scope = next(s for s in query if isinstance(s, ScopeSnippet))
    assert Product.SOLAR_GRID in scope.scopes
    assert BoostSnippet(Product.SOLAR_GRID, 1.2) in query
    assert BoostSnippet(Product.OREM, 1.2) not in query
    assert any(isinstance(s, PinSnippet) and s.scope is Product.SOLAR_GRID for s in query)
    assert not any(isinstance(s, PinSnippet) and s.scope is Product.OREM for s in query)
    solar_chunk = chunk(
        "solar#вечер",
        "эффективность сетевой солнечной станции при вечернем потреблении",
        Product.SOLAR_GRID,
    )
    result = RetrieveKnowledge(get_config("bm25"), toy_index((*CORPUS, solar_chunk)))(query)
    assert any(e.chunk.scope is Product.SOLAR_GRID for e in result.evidence)


def test_id15_full_query_keeps_mixed_collection_history_for_recall_and_rerank() -> None:
    state = ConversationState(
        "id15",
        history=[
            Message(Role.USER, "Хотим подключить приборы в бизнес-центре.", 1),
            Message(Role.ASSISTANT, "Общий учёт или расходы по арендаторам?", 2),
            Message(Role.USER, "По арендаторам, у большинства счётчики уже стоят.", 3),
            Message(Role.ASSISTANT, "Как сейчас собираете показания?", 4),
            Message(Role.USER, "Кто-то присылает фото, а по некоторым ходит техник.", 5),
            Message(Role.USER, "С чего нам начать?", 6),
        ],
    )
    extraction = Extraction(
        Intent.MIXED,
        mentions=(Product.METERING,),
        search_query="подключение приборов учёта в бизнес-центре по арендаторам",
    )

    query = build_query(extraction, state, decision(leader=Product.METERING), BUDGET)
    recall_text = "\n".join(
        s.text for s in query if isinstance(s, TextSnippet) and s.role == "query"
    ).lower()
    rerank_text = parse_query(query).rerank_query.lower()

    for expected in ("арендаторам", "фото", "техник"):
        assert expected in recall_text
        assert expected in rerank_text

    photo_chunk = chunk(
        "photo#ручной-сбор",
        "фото, показания ручные обходы техник. распознавание",
        Product.PHOTO,
    )
    batch_query = build_query(
        extraction,
        state,
        decision(leader=Product.METERING),
        BudgetSnippet(top_k=4),
        restrict_scope=False,
    )
    result = RetrieveKnowledge(get_config("bm25"), toy_index((*CORPUS, photo_chunk)))(batch_query)
    scopes = {e.chunk.scope for e in result.evidence if e.origin == "ranked"}
    assert {Product.METERING, Product.PHOTO} <= scopes


def test_id15_photo_collection_history_expands_restricted_scope() -> None:
    state = ConversationState(
        "id15-scope",
        history=[
            Message(Role.USER, "Нужен учёт по арендаторам.", 1),
            Message(Role.USER, "Часть присылает фото, остальных обходит техник.", 2),
            Message(Role.USER, "С чего начать подключение?", 3),
        ],
    )
    extraction = Extraction(
        Intent.MIXED,
        mentions=(Product.METERING,),
        search_query="подключение приборов учёта по арендаторам",
    )

    query = build_query(extraction, state, decision(leader=Product.METERING), BUDGET)

    scope = next(s for s in query if isinstance(s, ScopeSnippet))
    assert Product.METERING in scope.scopes
    assert Product.PHOTO in scope.scopes


def test_id15_saved_photo_collection_expands_restricted_scope() -> None:
    state = ConversationState(
        "id15-saved-scope",
        slots={
            "collection_method": SlotValue(
                "collection_method",
                "часть по фото, часть собирает техник обходом",
                SlotStatus.FILLED,
                "Кто-то присылает фото, по остальным ходит техник",
                2,
            )
        },
        history=[Message(Role.USER, "С чего начать подключение приборов?", 3)],
    )
    extraction = Extraction(
        Intent.MIXED,
        mentions=(Product.METERING,),
        search_query="подключение приборов учёта по арендаторам",
    )

    query = build_query(extraction, state, decision(leader=Product.METERING), BUDGET)

    scope = next(s for s in query if isinstance(s, ScopeSnippet))
    assert Product.PHOTO in scope.scopes


def test_full_query_bounds_history_but_keeps_recent_scenario_facts() -> None:
    old_history = [
        Message(Role.USER, f"старый нерелевантный факт {turn} " + "шум " * 100, turn)
        for turn in range(1, 20)
    ]
    state = ConversationState(
        "bounded-history",
        history=[
            *old_history,
            Message(Role.USER, "Учёт нужен по арендаторам.", 20),
            Message(Role.USER, "Показания присылают фотографиями.", 21),
            Message(Role.USER, "По части приборов делает ручной обход техник.", 22),
            Message(Role.USER, "С чего начать?", 23),
        ],
    )
    extraction = Extraction(
        Intent.MIXED,
        mentions=(Product.METERING,),
        search_query="подключение приборов учёта",
    )

    query = build_query(extraction, state, decision(leader=Product.METERING), BUDGET)
    history_snippets = [
        s.text
        for s in query
        if isinstance(s, TextSnippet)
        and (s.role == "context" or (s.weight == 0.5 and s.text != "С чего начать?"))
    ]
    history_text = "\n".join(history_snippets).lower()

    assert len(history_snippets) == 2
    assert all(len(text) <= 1_200 for text in history_snippets)
    assert "старый нерелевантный факт" not in history_text
    for expected in ("арендаторам", "фотографиями", "обход", "техник"):
        assert expected in history_text
