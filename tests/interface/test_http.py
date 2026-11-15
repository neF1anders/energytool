"""HTTP API на настоящих use cases и SQLite с фейковым OpenAI-совместимым прокси."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from energytool.bootstrap import assemble
from energytool.bootstrap_web import build_web
from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.gateway import LlmGateway
from energytool.interface.http.app import create_app

KB = {
    "20-products/21-orem.md": "# ОРЭМ\n\n## Кому подходит\n\nОт 750 кВт присоединённой мощности.\n"
    "\n## Чего утверждать нельзя\n\nНельзя обещать конкретную экономию.\n",
    "20-products/23-solar.md": "# Солнце\n\n## Размещение\n\nМонтаж на кровлю и фасад.\n",
}
EXTRACTION = {
    "intent": "mixed",
    "slot_updates": [
        {"slot_id": "segment", "value": "industry", "quote": "мы завод"},
        {"slot_id": "max_power_kw", "value": 1.1, "quote": "1,1 МВт", "unit": "МВт"},
    ],
    "search_query": "выход на оптовый рынок для завода",
}


def fake_proxy(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        data = [
            {"index": i, "embedding": [1.0, float(len(t) % 5)]}
            for i, t in enumerate(payload["input"])
        ]
        return httpx.Response(200, json={"data": data})
    if request.url.path.endswith("/rerank"):
        n = len(payload["documents"])
        return httpx.Response(
            200, json={"results": [{"index": i, "relevance_score": 0.9 - i / 10} for i in range(n)]}
        )
    system = payload["messages"][0]["content"]
    content = (
        json.dumps(EXTRACTION)
        if "извлекатель фактов" in system
        else "Да, вам подходит оптовый рынок. Какая у вас мощность?"
    )
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    kb = tmp_path / "knowledge-base"
    for name, text in KB.items():
        (kb / name).parent.mkdir(parents=True, exist_ok=True)
        (kb / name).write_text(text, encoding="utf-8")
    settings = Settings.from_env(
        {
            "LLM_BASE_URL": "http://proxy/v1",
            "LLM_API_KEY": "k",
            "LLM_MODEL": "qwen",
            "SQLITE_PATH": str(tmp_path / "db.sqlite"),
            "LEADS_DIR": str(tmp_path / "leads"),
        }
    )
    gateway = LlmGateway(settings, client=httpx.Client(transport=httpx.MockTransport(fake_proxy)))
    container = assemble(settings, gateway)

    def services():
        return build_web(container, container.build_index(kb))

    with TestClient(create_app(services)) as test_client:
        yield test_client


def new_session(client: TestClient) -> str:
    return str(client.post("/api/sessions").json()["session_id"])


def test_health_and_static(client: TestClient) -> None:
    assert client.get("/healthz").json() == {
        "status": "ok",
        "chunks": 3,
        "dense_ready": True,
        "model": "qwen",
        "retrieval": "hybrid_rerank",
    }
    console = client.get("/")
    assert "/static/app.js" in console.text and 'id="leadForm"' in console.text
    app_js = client.get("/static/app.js")
    assert app_js.headers["content-type"].startswith("application/javascript")
    assert "/api/admin/traces" in app_js.text
    assert client.get("/static/styles.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/secret.py").status_code == 404
    assert "widget.js" in client.get("/widget-demo").text
    assert "et-launch" in client.get("/widget.js").text


def test_message_json_has_reply_and_debug(client: TestClient) -> None:
    sid = new_session(client)
    out = client.post(
        f"/api/sessions/{sid}/messages?stream=false",
        json={"text": "Мы завод, 1,1 МВт. Нам опт подходит?"},
    ).json()
    assert out["reply"].startswith("Да, вам подходит")
    debug = out["debug"]
    assert debug["dialog"]["leader"] == "orem"
    assert any(d["title"].startswith("ОРЭМ") for d in debug["documents"])
    assert {"extract", "retrieve", "respond", "total"} <= set(debug["timings_ms"])
    assert [c["kind"] for c in debug["llm_calls"]].count("chat") == 2
    assert any(h["id"] == "OREM_POWER" for h in debug["rule_hits"])


def test_sse_streams_tokens_then_meta(client: TestClient) -> None:
    sid = new_session(client)
    with client.stream(
        "POST", f"/api/sessions/{sid}/messages", json={"text": "Мы завод, 1,1 МВт"}
    ) as r:
        body = "".join(r.iter_text())
    events = [block.split("\n")[0] for block in body.strip().split("\n\n")]
    assert events[0] == "event: token" and events[-2:] == ["event: meta", "event: done"]


def test_history_debug_reset_and_lead(client: TestClient) -> None:
    sid = new_session(client)
    client.post(f"/api/sessions/{sid}/messages?stream=false", json={"text": "Мы завод, 1,1 МВт"})
    assert [m["role"] for m in client.get(f"/api/sessions/{sid}/messages").json()] == [
        "user",
        "assistant",
    ]
    assert len(client.get(f"/api/sessions/{sid}/debug").json()) == 1

    bad = client.post(
        f"/api/sessions/{sid}/lead",
        json={"name": "Иван", "phone": "123", "company": "Завод", "pd_consent": True},
    )
    assert bad.status_code == 422
    ok = client.post(
        f"/api/sessions/{sid}/lead",
        json={"name": "Иван", "phone": "+7 912 345-67-89", "company": "Завод", "pd_consent": True},
    ).json()
    assert ok["lead_id"] and ok["dialog"] == "submitted"
    leads = client.get("/api/leads").json()
    assert leads[0]["status"] == "mock_sent"
    assert "Выход на оптовый рынок" in leads[0]["brief_text"]
    assert "мощность 1 100 кВт" in leads[0]["brief_text"]
    assert "Завод" in client.get("/leads").text

    new_sid = client.post(f"/api/sessions/{sid}/reset").json()["session_id"]
    assert new_sid != sid
    assert client.get(f"/api/sessions/{new_sid}/messages").json() == []
    assert (
        len(client.get(f"/api/sessions/{sid}/messages").json()) == 4
    )  # история старой сессии сохранена


def test_unknown_session_is_404(client: TestClient) -> None:
    assert client.post("/api/sessions/nope/messages", json={"text": "x"}).status_code == 404
    assert (
        client.post(
            "/api/sessions/nope/lead",
            json={"name": "a", "phone": "+79123456789", "company": "b", "pd_consent": True},
        ).status_code
        == 404
    )


def test_trace_feed_lists_all_sessions_with_stats(client: TestClient) -> None:
    for text in ("Мы завод, 1,1 МВт", "Нам опт подходит?"):
        sid = new_session(client)
        client.post(f"/api/sessions/{sid}/messages?stream=false", json={"text": text})
    feed = client.get("/api/admin/traces?limit=10").json()
    assert feed["stats"]["requests"] == 2
    assert feed["stats"]["avg_ms"] > 0 and feed["stats"]["last_ms"] > 0
    assert feed["stats"]["llm_calls"] == sum(len(t["llm_calls"]) for t in feed["items"])
    newest = feed["items"][0]
    assert newest["session_id"] == sid and newest["created_at"]
    assert any(d["title"].startswith("ОРЭМ") for d in newest["documents"])
    since = newest["created_at"]
    assert client.get("/api/admin/traces", params={"since": since}).json()["items"] == []


def test_lead_is_viewable_and_downloadable_as_txt(client: TestClient, tmp_path: Path) -> None:
    sid = new_session(client)
    client.post(f"/api/sessions/{sid}/messages?stream=false", json={"text": "Мы завод, 1,1 МВт"})
    lead_id = client.post(
        f"/api/sessions/{sid}/lead",
        json={"name": "Иван", "phone": "+7 912 345-67-89", "company": "Завод", "pd_consent": True},
    ).json()["lead_id"]

    lead = client.get(f"/api/leads/{lead_id}").json()
    assert lead["session_id"] == sid and lead["brief"]["contact"]["company"] == "Завод"

    download = client.get(f"/api/leads/{lead_id}.txt")
    assert download.headers["content-type"].startswith("text/plain")
    assert f'filename="lead-{lead_id}.txt"' in download.headers["content-disposition"]
    assert download.text.startswith(f"Заявка Energytool #{lead_id}")
    assert lead["brief_text"].strip() in download.text

    on_disk = (tmp_path / "leads" / f"lead-{lead_id}.txt").read_text(encoding="utf-8")
    assert on_disk == download.text

    assert client.get("/api/leads/nope").status_code == 404
    assert client.get("/api/leads/nope.txt").status_code == 404
