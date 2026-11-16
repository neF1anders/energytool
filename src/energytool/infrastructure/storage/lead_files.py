"""Заявка текстовым файлом: `<LEADS_DIR>/<lead_id>.txt` — бриф, который менеджер открывает
без CRM и без базы. Тот же текст отдаёт GET /api/leads/{id}.txt.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from energytool.application.ports import LeadSink
from energytool.domain.lead import Lead

logger = logging.getLogger(__name__)


def render_lead_file(lead_id: str, session_id: str, created_at: datetime, brief_text: str) -> str:
    header = (
        f"Заявка Energytool #{lead_id}\n"
        f"Создана: {created_at:%Y-%m-%d %H:%M} UTC\n"
        f"Сессия диалога: {session_id}\n"
    )
    return f"{header}\n{brief_text.rstrip()}\n"


def lead_file_name(lead_id: str) -> str:
    return f"lead-{lead_id}.txt"


class TextFileLeadSink:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def send(self, lead: Lead) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / lead_file_name(lead.id)
        text = render_lead_file(lead.id, lead.brief.session_id, lead.brief.created_at, lead.text)
        path.write_text(text, encoding="utf-8")
        logger.info("lead file written", extra={"data": {"lead_id": lead.id, "path": str(path)}})


class CompositeLeadSink:
    """Основной sink обязателен (его ошибка уходит наружу), дополнительные — best effort."""

    def __init__(self, primary: LeadSink, extras: Sequence[LeadSink]) -> None:
        self._primary = primary
        self._extras = tuple(extras)

    def send(self, lead: Lead) -> None:
        self._primary.send(lead)
        for sink in self._extras:
            try:
                sink.send(lead)
            except OSError:
                logger.exception("extra lead sink failed", extra={"data": {"lead_id": lead.id}})
