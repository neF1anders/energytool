"""TurnTrace — телеметрия одного хода (веб) или одного вопроса (батч).

Пишется JSON-строкой в лог и в таблицу traces; её же показывает режим отладки виджета.
Состав полей — docs/spec.md §8.5. Все поля с умолчаниями: этапы, до которых ход
не дошёл (ошибка, деградация), остаются пустыми, но трасса всё равно пишется.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

TraceMode = Literal["chat", "batch"]
LlmCallKind = Literal["chat", "embed", "rerank"]


@dataclass(frozen=True, slots=True)
class LlmCall:
    """Один вызов модели через gateway."""

    kind: LlmCallKind
    model: str
    ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    retries: int = 0
    ok: bool = True
    error: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    section_id: str
    origin: str
    score: float


@dataclass(frozen=True, slots=True)
class RuleHitRef:
    id: str
    weight: float
    kb_source: str


@dataclass(slots=True)
class TurnTrace:
    """Изменяемый накопитель: use case дополняет его по шагам хода."""

    session_id: str
    turn: int
    mode: TraceMode
    timings_ms: dict[str, float] = field(default_factory=dict)
    llm_calls: list[LlmCall] = field(default_factory=list)
    extraction: dict[str, object] = field(default_factory=dict)
    dialog: dict[str, str] = field(default_factory=dict)  # from, to, reason
    scores_before: dict[str, float] = field(default_factory=dict)
    scores_after: dict[str, float] = field(default_factory=dict)
    rule_hits: list[RuleHitRef] = field(default_factory=list)
    next_slot: str | None = None
    need_facts: bool = False
    evidence: list[EvidenceRef] = field(default_factory=list)
    below_floor: bool = False
    fallback_used: bool = False
    degraded: list[str] = field(default_factory=list)  # какие шаги ушли в деградацию
    reply_words: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
