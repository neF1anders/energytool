"""State Reducer (spec §5.2): Extraction → новое состояние. Чистая функция."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from energytool.domain.contract import ContractRegistry
from energytool.domain.extraction import Extraction, SlotUpdate
from energytool.domain.slots import (
    DocumentStatus,
    SlotScalar,
    SlotSpec,
    SlotStatus,
    SlotType,
    SlotValue,
)
from energytool.domain.state import ConversationState, SoftSignal

QUOTE_MATCH_THRESHOLD = 0.85
MENTION_SIGNAL = 1.0

_NUMBER = re.compile(r"-?\d[\d\s]*(?:[.,]\d+)?")
_TRUE = {"true", "да", "yes", "есть", "1"}
_FALSE = {"false", "нет", "no", "0"}


@dataclass(frozen=True, slots=True)
class Rejection:
    slot_id: str
    reason: str  # unknown_slot | no_quote | quote_mismatch | bad_value | unit_mismatch


@dataclass(frozen=True, slots=True)
class Reduction:
    state: ConversationState
    accepted: tuple[str, ...]
    rejected: tuple[Rejection, ...]
    progressed: bool


def apply(
    state: ConversationState, extraction: Extraction, registry: ContractRegistry, turn_text: str
) -> Reduction:
    slots = dict(state.slots)
    accepted: list[str] = []
    rejected: list[Rejection] = []
    for update in extraction.slot_updates:
        outcome = _accept(update, registry, turn_text, state.turn)
        if isinstance(outcome, Rejection):
            rejected.append(outcome)
            continue
        old = slots.get(update.slot_id)
        if old is not None and old.status is SlotStatus.FILLED and old.value == outcome.value:
            continue
        previous = old if old is not None and old.status is SlotStatus.FILLED else None
        slots[update.slot_id] = replace(outcome, previous=previous)
        accepted.append(update.slot_id)
    for slot_id in extraction.declined:
        if not registry.has(slot_id):
            rejected.append(Rejection(slot_id, "unknown_slot"))
            continue
        current = slots.get(slot_id)
        if current is None or current.status is SlotStatus.EMPTY:
            slots[slot_id] = SlotValue(slot_id, None, SlotStatus.DECLINED, "", state.turn)
            accepted.append(slot_id)
    progressed = any(not registry.get(slot_id).contact for slot_id in accepted)
    signals = list(state.soft_signals) + [
        SoftSignal(product=p, weight=MENTION_SIGNAL, turn=state.turn, source="mention")
        for p in extraction.mentions
    ]
    new_state = replace(
        state,
        slots=slots,
        soft_signals=signals,
        turns_without_progress=0 if progressed else state.turns_without_progress + 1,
        asked=dict(state.asked),
        leader_history=list(state.leader_history),
        history=list(state.history),
    )
    return Reduction(new_state, tuple(accepted), tuple(rejected), progressed)


def _accept(
    update: SlotUpdate, registry: ContractRegistry, turn_text: str, turn: int
) -> SlotValue | Rejection:
    if not registry.has(update.slot_id):
        return Rejection(update.slot_id, "unknown_slot")
    if not update.quote.strip():
        return Rejection(update.slot_id, "no_quote")
    if not quote_in_text(update.quote, turn_text):
        return Rejection(update.slot_id, "quote_mismatch")
    spec = registry.get(update.slot_id)
    value = coerce(spec, update.value, update.unit, update.quote)
    if isinstance(value, Rejection):
        return value
    return SlotValue(update.slot_id, value, SlotStatus.FILLED, update.quote.strip(), turn)


def quote_in_text(quote: str, text: str, threshold: float = QUOTE_MATCH_THRESHOLD) -> bool:
    """Частичное нечёткое совпадение: лучшая подстрока текста длиной с цитату."""
    q, t = _normalize(quote), _normalize(text)
    if not q:
        return False
    if q in t:
        return True
    if len(q) > len(t):
        return SequenceMatcher(None, q, t).ratio() >= threshold
    best = 0.0
    step = max(1, len(q) // 4)
    for start in range(0, len(t) - len(q) + 1, step):
        best = max(best, SequenceMatcher(None, q, t[start : start + len(q)]).ratio())
        if best >= threshold:
            return True
    return best >= threshold


def coerce(
    spec: SlotSpec, value: SlotScalar | None, unit: str | None, quote: str = ""
) -> SlotScalar | Rejection:
    """Значение Extractor → тип и базовая единица слота."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return Rejection(spec.id, "bad_value")
    match spec.type:
        case SlotType.NUMBER | SlotType.INTEGER:
            return _coerce_number(spec, value, unit, quote)
        case SlotType.BOOL:
            return _coerce_bool(spec, value)
        case SlotType.CHOICE:
            raw = str(value).strip().lower()
            choices = {str(c).lower(): str(c) for c in spec.choices}
            return str(choices[raw]) if raw in choices else Rejection(spec.id, "bad_value")
        case SlotType.DOCUMENT:
            try:
                return DocumentStatus(str(value).strip().lower()).value
            except ValueError:
                return Rejection(spec.id, "bad_value")
        case _:
            return str(value).strip()


def _coerce_number(
    spec: SlotSpec, value: SlotScalar, unit: str | None, quote: str
) -> SlotScalar | Rejection:
    if isinstance(value, bool):
        return Rejection(spec.id, "bad_value")
    context = " ".join(x for x in (str(value), unit or "", quote) if x).lower()
    if spec.unit and "кВт·ч" in spec.unit and re.search(r"руб|₽|р\.", context):
        return Rejection(spec.id, "unit_mismatch")
    number = _number_from_quote(value, unit, quote)
    if number is None:
        return Rejection(spec.id, "bad_value")
    if spec.unit == "кВт·ч/мес" and re.search(r"год|/г\b|в г\.", (unit or "") + " " + str(value)):
        number /= 12
    if number < 0:
        return Rejection(spec.id, "bad_value")
    if spec.type is SlotType.INTEGER:
        return round(number)
    return round(number, 3)


def _number_from_quote(value: SlotScalar, unit: str | None, quote: str) -> float | None:
    """Число в базовой единице. Источник правды — цитата клиента: модель иногда сама
    пересчитывает «1,1 МВт» в 1100 и при этом возвращает unit="МВт".

    1. значение модели совпадает с числом цитаты → множитель из слов сразу после него;
    2. значение модели = число цитаты × 10³/10⁶ → модель уже пересчитала, берём как есть;
    3. иначе — значение модели с множителем из её значения и единицы."""
    raw = float(value) if isinstance(value, int | float) else _parse_number(str(value))
    value_text = "" if isinstance(value, int | float) else str(value)
    for match in _NUMBER.finditer(quote):
        quoted = _to_float(match.group())
        if quoted is None or raw is None:
            continue
        tail = quote[match.end() : match.end() + 16]
        if abs(quoted - raw) < 1e-9:
            return quoted * _multiplier(value_text, tail)
        if quoted > 0 and any(abs(raw - quoted * f) < 1e-6 * raw for f in (1e3, 1e6)):
            return raw
    if raw is None:
        return None
    return raw * _multiplier(value_text, unit or "")


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _parse_number(text: str) -> float | None:
    match = _NUMBER.search(text)
    if not match:
        return None
    try:
        return float(match.group().replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _multiplier(value_text: str, unit: str) -> float:
    """Порядок величины из текста значения и единицы клиента относительно базовой единицы
    слота (кВт, кВт·ч): МВт/МВт·ч → ×1000, ГВт·ч → ×10⁶, «тыс.»/«млн» — множители."""
    text = f"{value_text} {unit}".lower().replace("ё", "е")
    factor = 1.0
    if re.search(r"\bмлн|миллион", text):
        factor *= 1_000_000
    elif re.search(r"\bтыс|тысяч", text):
        factor *= 1_000
    if re.search(r"гвт", text):
        factor *= 1_000_000
    elif re.search(r"мвт", text):
        factor *= 1_000
    return factor


def _coerce_bool(spec: SlotSpec, value: SlotScalar) -> SlotScalar | Rejection:
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return Rejection(spec.id, "bad_value")


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower().replace("ё", "е")).split())
