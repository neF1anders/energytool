"""ConversationState ⇄ JSON для Session Store. Формат версионируется полем "v"."""

from __future__ import annotations

import json
from typing import Any

from energytool.domain.products import Product
from energytool.domain.slots import SlotStatus, SlotValue
from energytool.domain.state import ConversationState, DialogState, Message, Role, SoftSignal

FORMAT_VERSION = 1


class StateDecodeError(ValueError):
    """Сохранённое состояние нельзя прочитать текущим кодом."""


def encode(state: ConversationState) -> str:
    return json.dumps(
        {
            "v": FORMAT_VERSION,
            "session_id": state.session_id,
            "turn": state.turn,
            "dialog": state.dialog.value,
            "slots": {k: _slot(v) for k, v in state.slots.items()},
            "soft_signals": [
                [s.product.value, s.weight, s.turn, s.source] for s in state.soft_signals
            ],
            "asked": state.asked,
            "last_cta_turn": state.last_cta_turn,
            "turns_without_progress": state.turns_without_progress,
            "leader_history": [p.value if p else None for p in state.leader_history],
            "history": [[m.role.value, m.text, m.turn] for m in state.history],
        },
        ensure_ascii=False,
    )


def decode(raw: str) -> ConversationState:
    try:
        data = json.loads(raw)
        if data.get("v") != FORMAT_VERSION:
            raise StateDecodeError(f"версия состояния {data.get('v')} ≠ {FORMAT_VERSION}")
        return ConversationState(
            session_id=data["session_id"],
            turn=int(data["turn"]),
            dialog=DialogState(data["dialog"]),
            slots={k: _slot_value(v, k) for k, v in data["slots"].items()},
            soft_signals=[
                SoftSignal(Product(p), float(w), int(t), str(src))
                for p, w, t, src in data["soft_signals"]
            ],
            asked={str(k): int(v) for k, v in data["asked"].items()},
            last_cta_turn=data["last_cta_turn"],
            turns_without_progress=int(data["turns_without_progress"]),
            leader_history=[Product(p) if p else None for p in data["leader_history"]],
            history=[Message(Role(r), str(t), int(n)) for r, t, n in data["history"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StateDecodeError(str(exc)) from exc


def _slot(value: SlotValue) -> dict[str, Any]:
    return {
        "value": value.value,
        "status": value.status.value,
        "quote": value.quote,
        "turn": value.turn,
        "previous": _slot(value.previous) if value.previous else None,
    }


def _slot_value(data: dict[str, Any], slot_id: str = "") -> SlotValue:
    return SlotValue(
        slot_id=data.get("slot_id", slot_id),
        value=data["value"],
        status=SlotStatus(data["status"]),
        quote=data["quote"],
        turn=int(data["turn"]),
        previous=_slot_value(data["previous"], slot_id) if data.get("previous") else None,
    )
