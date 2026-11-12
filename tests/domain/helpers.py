from energytool.domain.slots import SlotScalar, SlotStatus, SlotValue
from energytool.domain.state import ConversationState, DialogState


def state_with(
    turn: int = 1, dialog: DialogState = DialogState.DISCOVERY, **values: SlotScalar | None
) -> ConversationState:
    slots = {
        slot_id: SlotValue(
            slot_id, value, SlotStatus.FILLED if value is not None else SlotStatus.DECLINED, "q", 0
        )
        for slot_id, value in values.items()
    }
    return ConversationState(session_id="s", turn=turn, dialog=dialog, slots=slots)
