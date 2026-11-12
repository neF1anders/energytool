from energytool.domain import ALL_SLOTS, ContractRegistry


def test_registry_has_unique_slots() -> None:
    assert len(ContractRegistry().slots) == len(ALL_SLOTS)
