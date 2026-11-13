from collections.abc import Mapping

from energytool.domain.slots import SlotScalar, SlotStatus, SlotValue
from energytool.domain.state import ConversationState, DialogState


def state_from(
    values: Mapping[str, SlotScalar | None],
    turn: int = 1,
    dialog: DialogState = DialogState.DISCOVERY,
) -> ConversationState:
    """None в значении — клиент отказался ответить (DECLINED)."""
    slots = {
        slot_id: SlotValue(
            slot_id,
            value,
            SlotStatus.FILLED if value is not None else SlotStatus.DECLINED,
            "q",
            0,
        )
        for slot_id, value in values.items()
    }
    return ConversationState(session_id="s", turn=turn, dialog=dialog, slots=slots)


def state_with(**values: SlotScalar | None) -> ConversationState:
    return state_from(values)
