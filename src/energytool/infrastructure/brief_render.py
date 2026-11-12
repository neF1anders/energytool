"""BriefRenderer: Brief → текст для менеджера и JSON для CRM (spec §5.6).

Текст — по порядку разговора менеджера: кто клиент, что болит, что предлагаем и почему,
что исключили, что уже знаем (по пунктам опросного листа), чего не хватает, что
подготовить к звонку.
"""

from __future__ import annotations

from typing import Any

from energytool.domain.contract import ContractRegistry
from energytool.domain.lead import Brief
from energytool.domain.products import Product
from energytool.domain.slots import SlotStatus, SlotValue

PRODUCT_NAMES: dict[Product, str] = {
    Product.OREM: "Выход на оптовый рынок",
    Product.METERING: "Облачная платформа учёта",
    Product.SOLAR_GRID: "Сетевая солнечная станция",
    Product.SOLAR_HYBRID: "Гибридная солнечная станция с накопителями",
    Product.LOSSES: "Поиск коммерческих потерь",
    Product.PHOTO: "Распознавание показаний по фото",
}


class TextBriefRenderer:
    def __init__(self, registry: ContractRegistry | None = None) -> None:
        self._registry = registry or ContractRegistry()

    def render(self, brief: Brief) -> tuple[str, dict[str, object]]:
        return self._text(brief), self._payload(brief)

    def _text(self, brief: Brief) -> str:
        c = brief.contact
        lines = [
            f"Бриф по заявке · {brief.created_at:%d.%m.%Y %H:%M} UTC · сессия {brief.session_id}",
            "",
            f"Клиент: {c.name}, {c.company}, тел. {c.phone}"
            + (f", {c.email}" if c.email else "")
            + (f", {c.position}" if c.position else ""),
            f"Боль: {brief.pain or '—'}",
            "",
            "Подходящие решения:",
        ]
        for i, cand in enumerate(brief.candidates, 1):
            lines.append(f"  {i}. {PRODUCT_NAMES[cand.product]} — {cand.probability:.0%}")
            lines += [f"     + {r}" for r in cand.reasons]
            lines += [f"     ! {f}" for f in cand.flags]
        if brief.excluded:
            lines += ["", "Не подходит:"]
            lines += [
                f"  — {PRODUCT_NAMES[e.product]}: {e.reason} ({e.kb_source})"
                for e in brief.excluded
            ]
        lines += ["", "Анкета (по опросному листу):"]
        lines += [f"  {self._slot_line(v)}" for v in brief.slots] or ["  —"]
        if brief.missing:
            lines += ["", "Уточнить на звонке: " + ", ".join(self._title(s) for s in brief.missing)]
        if brief.documents_to_prepare:
            lines += ["", "Попросить подготовить: " + ", ".join(brief.documents_to_prepare)]
        lines += ["", f"Реплик клиента в чате: {brief.transcript_turns}"]
        return "\n".join(lines)

    def _payload(self, brief: Brief) -> dict[str, object]:
        c = brief.contact
        return {
            "session_id": brief.session_id,
            "created_at": brief.created_at.isoformat(),
            "contact": {
                "name": c.name,
                "phone": c.phone,
                "company": c.company,
                "email": c.email,
                "position": c.position,
                "inn": c.inn,
                "pd_consent": c.pd_consent,
            },
            "pain": brief.pain,
            "candidates": [
                {
                    "product": x.product.value,
                    "title": PRODUCT_NAMES[x.product],
                    "probability": x.probability,
                    "reasons": list(x.reasons),
                    "flags": list(x.flags),
                }
                for x in brief.candidates
            ],
            "excluded": [
                {"product": x.product.value, "reason": x.reason, "kb_source": x.kb_source}
                for x in brief.excluded
            ],
            "questionnaire": [self._slot_json(v) for v in brief.slots],
            "missing": list(brief.missing),
            "documents_to_prepare": list(brief.documents_to_prepare),
            "transcript_turns": brief.transcript_turns,
        }

    def _title(self, slot_id: str) -> str:
        return self._registry.get(slot_id).title if self._registry.has(slot_id) else slot_id

    def _slot_line(self, value: SlotValue) -> str:
        spec = self._registry.get(value.slot_id)
        item = f"п.{spec.opros_item} " if spec.opros_item else ""
        shown = (
            "не знает / не сказал" if value.status is SlotStatus.DECLINED else _format(value.value)
        )
        unit = f" {spec.unit}" if spec.unit and value.status is SlotStatus.FILLED else ""
        edited = f" (было: {_format(value.previous.value)})" if value.previous else ""
        quote = f" — «{value.quote}»" if value.quote and value.status is SlotStatus.FILLED else ""
        return f"{item}{spec.title}: {shown}{unit}{edited}{quote}"

    def _slot_json(self, value: SlotValue) -> dict[str, Any]:
        spec = self._registry.get(value.slot_id)
        return {
            "slot_id": value.slot_id,
            "title": spec.title,
            "opros_item": spec.opros_item,
            "value": value.value,
            "unit": spec.unit,
            "status": value.status.value,
            "quote": value.quote,
        }


def _format(value: object) -> str:
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}".replace(",", " ")
    if isinstance(value, int | float):
        return f"{value:,}".replace(",", " ")
    return str(value)
