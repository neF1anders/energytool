"""Маршруты HTTP API (spec §9.1). Тонкий слой: разбор входа → use case → ответ."""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Iterator
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse

from energytool.application.submit_lead import LeadValidationError, SessionNotFoundError
from energytool.bootstrap_web import WebServices
from energytool.domain.lead import Contact
from energytool.infrastructure.logs import log_context
from energytool.infrastructure.storage.lead_files import lead_file_name, render_lead_file
from energytool.interface.http.debug_view import debug_info, with_document_titles
from energytool.interface.http.schemas import (
    ContactIn,
    Health,
    LeadCreated,
    MessageIn,
    SessionCreated,
    TraceFeed,
    TraceStats,
    TurnOut,
)

logger = logging.getLogger(__name__)
router = APIRouter()
STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
CONSOLE = STATIC / "console"
CONSOLE_ASSETS = {"app.js": "application/javascript", "styles.css": "text/css"}
STREAM_CHUNK_WORDS = 3


def services(request: Request) -> WebServices:
    return request.app.state.services  # type: ignore[no-any-return]


@router.get("/healthz", response_model=Health)
def healthz(request: Request) -> Health:
    web = services(request)
    settings = web.container.settings
    return Health(
        status="ok",
        chunks=len(web.index.chunks.all()),
        dense_ready=web.index.dense_ready,
        model=settings.llm.model,
        retrieval=settings.retrieval_config,
    )


@router.post("/api/sessions", response_model=SessionCreated)
def create_session(request: Request) -> SessionCreated:
    return SessionCreated(session_id=services(request).sessions.create())


@router.post("/api/sessions/{session_id}/reset", response_model=SessionCreated)
def reset_session(session_id: str, request: Request) -> SessionCreated:
    repo = services(request).sessions
    _require_session(repo.exists(session_id), session_id)
    return SessionCreated(session_id=repo.replace(session_id))


@router.post("/api/sessions/{session_id}/messages", response_model=None)
def post_message(
    session_id: str, body: MessageIn, request: Request, stream: bool = True
) -> TurnOut | StreamingResponse:
    web = services(request)
    _require_session(web.sessions.exists(session_id), session_id)
    with log_context(session_id=session_id):
        response = web.handle_turn(session_id, body.text)
    out = TurnOut(
        session_id=session_id,
        reply=response.reply.text,
        dialog=response.dialog.value,
        lead_id=response.lead_id,
        debug=debug_info(response.trace, web.index),
    )
    if not stream:
        return out
    return StreamingResponse(_sse(out), media_type="text/event-stream")


@router.get("/api/sessions/{session_id}/messages")
def get_messages(session_id: str, request: Request) -> list[dict[str, Any]]:
    repo = services(request).sessions
    _require_session(repo.exists(session_id), session_id)
    return repo.messages(session_id)


@router.get("/api/sessions/{session_id}/debug")
def get_debug(session_id: str, request: Request) -> list[dict[str, Any]]:
    repo = services(request).sessions
    _require_session(repo.exists(session_id), session_id)
    return repo.traces(session_id)


@router.get("/api/admin/traces", response_model=TraceFeed)
def trace_feed(
    request: Request, limit: int = Query(50, ge=1, le=500), since: datetime | None = None
) -> TraceFeed:
    web = services(request)
    items = [with_document_titles(t, web.index) for t in web.sessions.recent_traces(limit, since)]
    totals = [float(t["timings_ms"].get("total", 0.0)) for t in items if t.get("timings_ms")]
    stats = TraceStats(
        requests=len(items),
        avg_ms=round(statistics.fmean(totals), 1) if totals else None,
        last_ms=totals[0] if totals else None,
        llm_calls=sum(len(t.get("llm_calls", [])) for t in items),
    )
    return TraceFeed(stats=stats, items=items)


@router.get("/", include_in_schema=False)
def console_page() -> FileResponse:
    return FileResponse(CONSOLE / "index.html", media_type="text/html")


@router.get("/static/{name}", include_in_schema=False)
def console_asset(name: str) -> FileResponse:
    if name not in CONSOLE_ASSETS:
        raise HTTPException(status_code=404, detail=f"{name} not found")
    return FileResponse(CONSOLE / name, media_type=CONSOLE_ASSETS[name])


@router.get("/widget-demo", include_in_schema=False)
def widget_demo_page() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html")


@router.get("/widget.js", include_in_schema=False)
def widget() -> FileResponse:
    return FileResponse(STATIC / "widget.js", media_type="application/javascript")


def _require_session(exists: bool, session_id: str) -> None:
    if not exists:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")


def _sse(out: TurnOut) -> Iterator[str]:
    """Ответ уже готов целиком (gateway не стримит токены): отдаём его кусками по словам,
    чтобы виджет печатал текст, затем событие meta с отладкой."""
    words = out.reply.split(" ")
    for start in range(0, len(words), STREAM_CHUNK_WORDS):
        chunk = " ".join(words[start : start + STREAM_CHUNK_WORDS])
        suffix = " " if start + STREAM_CHUNK_WORDS < len(words) else ""
        yield _event("token", json.dumps({"text": chunk + suffix}, ensure_ascii=False))
    yield _event("meta", out.model_dump_json())
    yield _event("done", "{}")


def _event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"


@router.post("/api/sessions/{session_id}/lead", response_model=LeadCreated)
def post_lead(session_id: str, body: ContactIn, request: Request) -> LeadCreated:
    web = services(request)
    contact = Contact(
        name=body.name,
        phone=body.phone,
        company=body.company,
        pd_consent=body.pd_consent,
        email=body.email,
        position=body.position,
    )
    with log_context(session_id=session_id):
        try:
            receipt = web.submit_lead.from_form(session_id, contact)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found") from exc
        except LeadValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LeadCreated(lead_id=receipt.lead_id, dialog="submitted", reply=receipt.reply)


@router.get("/api/leads")
def get_leads(request: Request, limit: int = 100) -> list[dict[str, Any]]:
    return services(request).leads.list(limit)


@router.get("/api/leads/{lead_id}.txt", response_class=PlainTextResponse)
def get_lead_file(lead_id: str, request: Request) -> PlainTextResponse:
    row = _require_lead(request, lead_id)
    text = render_lead_file(
        row["id"], row["session_id"], datetime.fromisoformat(row["created_at"]), row["brief_text"]
    )
    disposition = f'attachment; filename="{lead_file_name(row["id"])}"'
    return PlainTextResponse(text, headers={"Content-Disposition": disposition})


@router.get("/api/leads/{lead_id}")
def get_lead(lead_id: str, request: Request) -> dict[str, Any]:
    return _require_lead(request, lead_id)


def _require_lead(request: Request, lead_id: str) -> dict[str, Any]:
    row = services(request).leads.get(lead_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"lead {lead_id} not found")
    return row


@router.get("/leads", include_in_schema=False, response_class=HTMLResponse)
def leads_page(request: Request) -> str:
    """Страница менеджера: заявки с брифами, новые сверху."""
    rows = services(request).leads.list()
    cards = (
        "\n".join(
            f"<article><header><b>#{escape(r['id'])}</b> · {escape(r['created_at'][:16])} · "
            f"<span class='st'>{escape(r['status'])}</span> · "
            f"<a href='/api/sessions/{escape(r['session_id'])}/messages'>диалог</a> · "
            f"<a href='/api/sessions/{escape(r['session_id'])}/debug'>трасса</a></header>"
            f"<pre>{escape(r['brief_text'])}</pre></article>"
            for r in rows
        )
        or "<p>Заявок пока нет.</p>"
    )
    template = (STATIC / "leads.html").read_text(encoding="utf-8")
    return template.format(count=len(rows), cards=cards)
