"""SessionRepo на SQLite: состояние, реплики и трассы каждого хода."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, insert, select, update

from energytool.application.ports import ExternalServiceError
from energytool.application.trace import TurnTrace
from energytool.domain.state import ConversationState
from energytool.infrastructure.storage import database as db
from energytool.infrastructure.storage.state_codec import StateDecodeError, decode, encode

logger = logging.getLogger(__name__)


class StorageError(ExternalServiceError):
    """База недоступна или запись не удалась."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class SqliteSessionRepo:
    def __init__(self, engine: Engine, now: Callable[[], datetime] = utc_now) -> None:
        self._engine = engine
        self._now = now

    def create(self) -> str:
        session_id = uuid.uuid4().hex
        state = ConversationState(session_id=session_id)
        now = self._now()
        with self._engine.begin() as conn:
            conn.execute(
                insert(db.sessions).values(
                    id=session_id,
                    created_at=now,
                    updated_at=now,
                    dialog_state=state.dialog.value,
                    state_json=encode(state),
                )
            )
        logger.info("session created", extra={"data": {"session_id": session_id}})
        return session_id

    def replace(self, old_id: str) -> str:
        """«Новый диалог»: новая чистая сессия, старая сохраняется со ссылкой на новую."""
        new_id = self.create()
        with self._engine.begin() as conn:
            conn.execute(
                update(db.sessions).where(db.sessions.c.id == old_id).values(replaced_by=new_id)
            )
        logger.info("session reset", extra={"data": {"old": old_id, "new": new_id}})
        return new_id

    def exists(self, session_id: str) -> bool:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(db.sessions.c.id).where(db.sessions.c.id == session_id)
            ).first()
        return row is not None

    def load(self, session_id: str) -> ConversationState | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(db.sessions.c.state_json).where(db.sessions.c.id == session_id)
            ).first()
        if row is None:
            return None
        try:
            return decode(row.state_json)
        except StateDecodeError:
            logger.exception(
                "broken session state, starting clean", extra={"data": {"session_id": session_id}}
            )
            return None

    def save(self, state: ConversationState, trace: TurnTrace) -> None:
        now = self._now()
        new_messages = [m for m in state.history if m.turn == state.turn]
        try:
            with self._engine.begin() as conn:
                values = {
                    "updated_at": now,
                    "dialog_state": state.dialog.value,
                    "state_json": encode(state),
                }
                updated = conn.execute(
                    update(db.sessions).where(db.sessions.c.id == state.session_id).values(**values)
                ).rowcount
                if not updated:
                    conn.execute(
                        insert(db.sessions).values(id=state.session_id, created_at=now, **values)
                    )
                if new_messages:
                    conn.execute(
                        insert(db.messages),
                        [
                            {
                                "session_id": state.session_id,
                                "turn": m.turn,
                                "role": m.role.value,
                                "text": m.text,
                                "created_at": now,
                            }
                            for m in new_messages
                        ],
                    )
                conn.execute(
                    insert(db.traces).values(
                        session_id=state.session_id,
                        turn=trace.turn,
                        trace_json=json.dumps(trace.to_dict(), ensure_ascii=False, default=str),
                        created_at=now,
                    )
                )
        except Exception as exc:  # драйвер БД бросает свои типы; наружу — ошибка порта
            raise StorageError(f"save session {state.session_id}: {exc}") from exc

    def traces(self, session_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(db.traces.c.trace_json)
                .where(db.traces.c.session_id == session_id)
                .order_by(db.traces.c.id)
            ).all()
        return [json.loads(r.trace_json) for r in rows]

    def recent_traces(self, limit: int, since: datetime | None = None) -> list[dict[str, Any]]:
        """Последние трассы всех сессий, новые сверху — для панели мониторинга."""
        query = select(db.traces).order_by(db.traces.c.id.desc()).limit(limit)
        if since is not None:
            query = query.where(db.traces.c.created_at > as_utc(since).astimezone(UTC))
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        return [
            {**json.loads(r.trace_json), "created_at": as_utc(r.created_at).isoformat()}
            for r in rows
        ]

    def messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(db.messages.c.turn, db.messages.c.role, db.messages.c.text)
                .where(db.messages.c.session_id == session_id)
                .order_by(db.messages.c.id)
            ).all()
        return [{"turn": r.turn, "role": r.role, "text": r.text} for r in rows]


def as_utc(moment: datetime) -> datetime:
    # SQLite возвращает DateTime без tzinfo даже при timezone=True; пишем мы всегда UTC
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
