"""Схемы HTTP API (pydantic только на границе interface, домен — dataclasses)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreated(BaseModel):
    session_id: str


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=40)
    company: str = Field(min_length=1, max_length=300)
    pd_consent: bool
    email: str | None = None
    position: str | None = None


class LeadCreated(BaseModel):
    lead_id: str | None
    dialog: str
    reply: str


class DebugInfo(BaseModel):
    """То, что показывает режим отладки виджета: время, документы, правила, решение."""

    turn: int
    total_ms: float
    timings_ms: dict[str, float]
    documents: list[dict[str, Any]]
    rule_hits: list[dict[str, Any]]
    dialog: dict[str, str]
    scores: dict[str, float]
    next_slot: str | None
    llm_calls: list[dict[str, Any]]
    degraded: list[str]
    extraction: dict[str, Any]


class TurnOut(BaseModel):
    session_id: str
    reply: str
    dialog: str
    lead_id: str | None
    debug: DebugInfo


class Health(BaseModel):
    status: str
    chunks: int
    dense_ready: bool
