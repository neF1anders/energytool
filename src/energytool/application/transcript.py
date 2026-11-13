"""Разбор ячейки question этапа 1: одиночный вопрос или история диалога.

Формат истории в демо: строки «Пользователь: …» / «Ассистент: …». Строка без метки
продолжает предыдущую реплику. Нет ни одной метки — вся ячейка один вопрос клиента.
Метки сравниваются без регистра; поддержаны синонимы на случай другого оформления
в закрытой выборке.
"""

from __future__ import annotations

import re

from energytool.domain.state import Message, Role

_ROLE_LABELS: dict[str, Role] = {
    "пользователь": Role.USER,
    "клиент": Role.USER,
    "user": Role.USER,
    "ассистент": Role.ASSISTANT,
    "консультант": Role.ASSISTANT,
    "бот": Role.ASSISTANT,
    "assistant": Role.ASSISTANT,
}
_LABEL = re.compile(r"^\s*(" + "|".join(_ROLE_LABELS) + r")\s*:\s*(.*)$", re.IGNORECASE)


def parse_transcript(text: str) -> list[Message]:
    messages: list[tuple[Role, list[str]]] = []
    for line in text.strip().splitlines():
        match = _LABEL.match(line)
        if match:
            messages.append((_ROLE_LABELS[match.group(1).lower()], [match.group(2).strip()]))
        elif messages:
            messages[-1][1].append(line.strip())
        elif line.strip():
            messages.append((Role.USER, [line.strip()]))
    if not any(role is Role.ASSISTANT for role, _ in messages) and len(messages) > 1:
        # метки не найдены, но строк несколько: это один многострочный вопрос
        messages = [(Role.USER, [part for _, lines in messages for part in lines])]
    return [
        Message(role=role, text=" ".join(p for p in lines if p).strip(), turn=turn)
        for turn, (role, lines) in enumerate(messages)
        if any(lines)
    ]


def last_user_message(messages: list[Message]) -> str:
    return next((m.text for m in reversed(messages) if m.role is Role.USER), "")
