from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from energytool.application.trace import TurnTrace
from energytool.domain.brief import build, contact_from_state
from energytool.domain.contract import ContractRegistry
from energytool.domain.lead import Lead
from energytool.domain.products import Product
from energytool.domain.rules import PRIORS, RULES
from energytool.domain.scorer import score
from energytool.domain.scoring import ScoringParams
from energytool.domain.slots import SlotStatus, SlotValue
from energytool.domain.state import ConversationState, DialogState, Message, Role, SoftSignal
from energytool.infrastructure.brief_render import TextBriefRenderer
from energytool.infrastructure.storage.database import create_database
from energytool.infrastructure.storage.leads import SqliteLeadSink
from energytool.infrastructure.storage.sqlite_sessions import SqliteSessionRepo
from energytool.infrastructure.storage.state_codec import StateDecodeError, decode, encode
from tests.domain.helpers import state_with

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def rich_state() -> ConversationState:
    state = state_with(
        segment="industry",
        max_power_kw=1100.0,
        region=None,
        contact_name="Иван",
        phone="+79123456789",
        company="Хлебозавод",
        pd_consent=True,
    )
    old = state.slots["max_power_kw"]
    state.slots["max_power_kw"] = SlotValue(
        "max_power_kw",
        1100.0,
        SlotStatus.FILLED,
        "1,1 МВт",
        2,
        previous=SlotValue("max_power_kw", 600.0, SlotStatus.FILLED, "600", 1),
    )
    assert old.value == 1100.0
    state.dialog = DialogState.QUALIFY
    state.soft_signals.append(SoftSignal(Product.OREM, 1.0, 1, "mention"))
    state.asked["region"] = 1
    state.leader_history = [None, Product.OREM]
    state.history = [
        Message(Role.USER, "Мы хлебозавод", 1),
        Message(Role.ASSISTANT, "Какая мощность?", 1),
    ]
    return state


def test_state_codec_roundtrip() -> None:
    state = rich_state()
    assert decode(encode(state)) == state


def test_state_codec_rejects_other_version() -> None:
    with pytest.raises(StateDecodeError):
        decode('{"v": 99}')


def test_session_repo_saves_messages_and_traces(tmp_path: Path) -> None:
    repo = SqliteSessionRepo(create_database(tmp_path / "db.sqlite"))
    sid = repo.create()
    state = rich_state()
    state.session_id, state.turn = sid, 1
    repo.save(state, TurnTrace(sid, 1, "chat"))
    assert repo.load(sid) == state
    assert [m["role"] for m in repo.messages(sid)] == ["user", "assistant"]
    assert repo.traces(sid)[0]["turn"] == 1
    new = repo.replace(sid)
    assert repo.exists(new) and repo.load(new) is not None and repo.load(new).turn == 0  # type: ignore[union-attr]


def make_lead() -> Lead:
    state = rich_state()
    board = score(state, RULES, PRIORS, ScoringParams())
    brief = build(state, board, ContractRegistry(), contact_from_state(state), NOW)
    text, payload = TextBriefRenderer().render(brief)
    return Lead("L1", brief, text, payload)


def test_brief_text_and_payload() -> None:
    lead = make_lead()
    assert "Клиент: Иван, Хлебозавод, тел. +79123456789" in lead.text
    assert "1. Выход на оптовый рынок" in lead.text
    assert "Максимальная мощность" in lead.text or "мощность" in lead.text
    assert "(было: 600)" in lead.text
    assert "не знает / не сказал" in lead.text
    assert lead.payload["candidates"][0]["product"] == "orem"  # type: ignore[index]


@pytest.mark.parametrize(
    ("url", "status_code", "expected"),
    [
        ("", 200, "mock_sent"),
        ("http://crm/hook", 200, "sent"),
        ("http://crm/hook", 500, "send_failed"),
    ],
)
def test_lead_sink_statuses(tmp_path: Path, url: str, status_code: int, expected: str) -> None:
    engine = create_database(tmp_path / "db.sqlite")
    sessions = SqliteSessionRepo(engine)
    lead = make_lead()
    sessions.save(decode(encode(rich_state())), TurnTrace("s", 1, "chat"))
    posted: list[dict[str, object]] = []

    def crm(request: httpx.Request) -> httpx.Response:
        posted.append({"url": str(request.url)})
        return httpx.Response(status_code)

    sink = SqliteLeadSink(engine, url, client=httpx.Client(transport=httpx.MockTransport(crm)))
    sink.send(lead)
    (row,) = sink.list()
    assert row["status"] == expected
    assert row["brief"]["contact"]["company"] == "Хлебозавод"
    assert len(posted) == (1 if url else 0)
