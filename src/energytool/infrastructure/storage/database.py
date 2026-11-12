"""SQLite через SQLAlchemy Core: схема и движок (spec §8.4).

    sessions(id, created_at, updated_at, dialog_state, state_json)
    messages(id, session_id, turn, role, text, created_at)
    traces(id, session_id, turn, trace_json, created_at)
    leads(id, session_id, created_at, brief_text, brief_json, sent_at, status)

История всех сессий хранится, в том числе без заявки (требование кейса).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)

metadata = MetaData()

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("dialog_state", String(32), nullable=False),
    Column("state_json", Text, nullable=False),
    Column("replaced_by", String(64), nullable=True),  # «Новый диалог» → id новой сессии
)

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), ForeignKey("sessions.id"), index=True, nullable=False),
    Column("turn", Integer, nullable=False),
    Column("role", String(16), nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

traces = Table(
    "traces",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), ForeignKey("sessions.id"), index=True, nullable=False),
    Column("turn", Integer, nullable=False),
    Column("trace_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

leads = Table(
    "leads",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("sessions.id"), index=True, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("brief_text", Text, nullable=False),
    Column("brief_json", Text, nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=True),
    Column("status", String(32), nullable=False),
)


def create_database(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True
    )

    @event.listens_for(engine, "connect")
    def _pragmas(connection, _record):  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    return engine
