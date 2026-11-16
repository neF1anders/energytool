"""LeadSink: заявка всегда сохраняется в таблицу leads; при заданном CRM_WEBHOOK_URL —
ещё и POST JSON в CRM. Статусы: mock_sent (вебхука нет), sent, send_failed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import Engine, Row, insert, select, update

from energytool.domain.lead import Lead
from energytool.infrastructure.storage import database as db
from energytool.infrastructure.storage.sqlite_sessions import StorageError, as_utc, utc_now

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SEC = 10.0


class SqliteLeadSink:
    def __init__(
        self,
        engine: Engine,
        webhook_url: str = "",
        client: httpx.Client | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._engine = engine
        self._webhook_url = webhook_url
        self._client = client or httpx.Client(timeout=WEBHOOK_TIMEOUT_SEC)
        self._now = now

    def send(self, lead: Lead) -> None:
        self._store(lead)
        status = self._deliver(lead)
        with self._engine.begin() as conn:
            conn.execute(
                update(db.leads)
                .where(db.leads.c.id == lead.id)
                .values(status=status, sent_at=self._now() if status != "send_failed" else None)
            )
        logger.info("lead delivered", extra={"data": {"lead_id": lead.id, "status": status}})

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(db.leads).order_by(db.leads.c.created_at.desc()).limit(limit)
            ).all()
        return [_lead_row(r) for r in rows]

    def get(self, lead_id: str) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(db.leads).where(db.leads.c.id == lead_id)).first()
        return _lead_row(row) if row is not None else None

    def _store(self, lead: Lead) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(db.leads).values(
                        id=lead.id,
                        session_id=lead.brief.session_id,
                        created_at=self._now(),
                        brief_text=lead.text,
                        brief_json=json.dumps(lead.payload, ensure_ascii=False, default=str),
                        status="new",
                    )
                )
        except Exception as exc:  # драйвер БД бросает свои типы; наружу — ошибка порта
            raise StorageError(f"store lead {lead.id}: {exc}") from exc

    def _deliver(self, lead: Lead) -> str:
        if not self._webhook_url:
            return "mock_sent"
        try:
            response = self._client.post(
                self._webhook_url,
                json={"lead_id": lead.id, "brief": lead.payload, "text": lead.text},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("crm webhook failed: %s", exc, extra={"data": {"lead_id": lead.id}})
            return "send_failed"
        return "sent"


def _lead_row(row: Row[Any]) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "created_at": as_utc(row.created_at).isoformat(),
        "status": row.status,
        "brief_text": row.brief_text,
        "brief": json.loads(row.brief_json),
    }
