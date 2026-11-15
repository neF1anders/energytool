"""FactExtractor (spec §8.2): реплики → Extraction в JSON.

- каталог полей генерируется из Contract Registry (id, подпись, тип, варианты, единица);
- в промпт идут уже известные поля и последние 6 реплик;
- ответ — JSON в тексте (поддержка response_format на прокси платформы не гарантирована):
  вырезаем первый {...}, валидируем поля и значения, при ошибке разбора — один повтор;
- невалидные элементы отбрасываются поштучно, а не весь ответ.

Ошибка модели или разбора → ExternalServiceError наружу: use case уходит в деградацию
(Extraction(intent=QUESTION, search_query=последняя реплика)).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from energytool.application.ports import ExternalServiceError
from energytool.domain.extraction import Extraction, Intent, SlotUpdate
from energytool.domain.products import Product
from energytool.domain.slots import SlotScalar, SlotSpec, SlotStatus, SlotType
from energytool.domain.state import ConversationState, Message, Role
from energytool.infrastructure.labels import CHOICE_LABELS
from energytool.infrastructure.llm.gateway import ChatMessage, LlmGateway
from energytool.infrastructure.llm.prompts import (
    EXTRACTOR_SYSTEM,
    EXTRACTOR_USER,
    EXTRACTOR_VERSION,
)

logger = logging.getLogger(__name__)

HISTORY_MESSAGES = 6
EXTRACTOR_MAX_TOKENS = 900
_PRODUCT_LABELS = {
    Product.OREM: "оптовый рынок",
    Product.METERING: "платформа учёта",
    Product.SOLAR_GRID: "сетевая солнечная станция",
    Product.SOLAR_HYBRID: "гибридная станция с накопителями",
    Product.LOSSES: "поиск потерь",
    Product.PHOTO: "показания по фото",
}


class ExtractionParseError(ExternalServiceError):
    """Модель вернула ответ, из которого не удалось собрать Extraction."""


class LlmFactExtractor:
    def __init__(self, gateway: LlmGateway, max_tokens: int = EXTRACTOR_MAX_TOKENS) -> None:
        self._gateway = gateway
        self._max_tokens = max_tokens

    def extract(
        self, history: Sequence[Message], state: ConversationState, catalog: Sequence[SlotSpec]
    ) -> Extraction:
        messages = build_messages(history, state, catalog)
        last_error = ""
        for attempt in range(2):
            result = self._gateway.chat(messages, temperature=0.0, max_tokens=self._max_tokens)
            try:
                extraction = parse_extraction(result.text, catalog)
            except ExtractionParseError as exc:
                last_error = str(exc)
                logger.warning("extraction parse failed (attempt %d): %s", attempt + 1, exc)
                continue
            logger.info(
                "extraction",
                extra={
                    "data": {
                        "prompt": EXTRACTOR_VERSION,
                        "intent": extraction.intent.value,
                        "slots": [(u.slot_id, u.value) for u in extraction.slot_updates],
                        "declined": list(extraction.declined),
                        "mentions": [p.value for p in extraction.mentions],
                        "rejected": [p.value for p in extraction.rejected],
                        "search_query": extraction.search_query,
                    }
                },
            )
            return extraction
        raise ExtractionParseError(last_error)


def build_messages(
    history: Sequence[Message], state: ConversationState, catalog: Sequence[SlotSpec]
) -> list[ChatMessage]:
    system = EXTRACTOR_SYSTEM.format(
        products=", ".join(f"{p.value} ({label})" for p, label in _PRODUCT_LABELS.items()),
        catalog="\n".join(_catalog_line(spec) for spec in catalog),
    )
    names = {Role.USER: "Клиент", Role.ASSISTANT: "Консультант"}
    dialog = "\n".join(f"{names[m.role]}: {m.text}" for m in history[-HISTORY_MESSAGES:])
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": EXTRACTOR_USER.format(known=_known(state), dialog=dialog)},
    ]


def _catalog_line(spec: SlotSpec) -> str:
    if spec.type is SlotType.CHOICE or spec.type is SlotType.DOCUMENT:
        options = spec.choices or ("have", "no", "later")
        detail = "; ".join(f"{c}={CHOICE_LABELS[c]}" if c in CHOICE_LABELS else c for c in options)
    elif spec.unit:
        detail = f"число, {spec.unit}"
    else:
        detail = {SlotType.BOOL: "true/false", SlotType.INTEGER: "целое"}.get(spec.type, "текст")
    return f"{spec.id} | {spec.title} | {spec.type.value} | {detail}"


def _known(state: ConversationState) -> str:
    filled = [
        f"{v.slot_id}={v.value}" for v in state.slots.values() if v.status is SlotStatus.FILLED
    ]
    return ", ".join(filled) if filled else "ничего"


def parse_extraction(text: str, catalog: Sequence[SlotSpec]) -> Extraction:
    data = _json_object(text)
    known = {spec.id for spec in catalog}
    try:
        intent = Intent(str(data.get("intent", "question")).strip().lower())
    except ValueError:
        intent = Intent.QUESTION
    updates = tuple(u for u in (_update(item, known) for item in _list(data, "slot_updates")) if u)
    return Extraction(
        intent=intent,
        slot_updates=updates,
        declined=tuple(str(x) for x in _list(data, "declined") if str(x) in known),
        mentions=tuple(_products(_list(data, "mentions"))),
        rejected=tuple(_products(_list(data, "rejected"))),
        wants_contact=bool(data.get("wants_contact", False)),
        search_query=str(data.get("search_query") or "").strip(),
        question=str(data.get("question") or "").strip(),
        extras={"prompt": EXTRACTOR_VERSION},
    )


def _json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ExtractionParseError(f"нет JSON-объекта: {text[:200]!r}")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ExtractionParseError(f"битый JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionParseError("JSON не объект")
    return data


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key) or []
    return value if isinstance(value, list) else []


def _update(item: object, known: set[str]) -> SlotUpdate | None:
    if not isinstance(item, dict):
        return None
    slot_id = str(item.get("slot_id", ""))
    value = item.get("value")
    if slot_id not in known or not isinstance(value, str | int | float | bool):
        return None
    unit = item.get("unit")
    scalar: SlotScalar = value
    return SlotUpdate(
        slot_id=slot_id,
        value=scalar,
        quote=str(item.get("quote") or ""),
        unit=str(unit) if unit else None,
    )


def _products(values: list[Any]) -> list[Product]:
    products = []
    for value in values:
        try:
            products.append(Product(str(value)))
        except ValueError:
            continue
    return products
