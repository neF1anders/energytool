"""Решение Dialogue Policy на ход и пороги автомата.

Policy — чистая функция: (state, extraction, scoreboard, registry, thresholds) → PolicyDecision.
"""

from __future__ import annotations

from dataclasses import dataclass

from energytool.domain.products import Product
from energytool.domain.state import DialogState


@dataclass(frozen=True, slots=True)
class Thresholds:
    leader_probability: float = 0.45       # DISCOVERY → QUALIFY: есть лидер…
    leader_margin: float = 0.15            # …с отрывом от второго
    candidate_probability: float = 0.25    # кандидат = активный блок анкеты (максимум 2)
    max_candidates: int = 2
    ready_completeness: float = 0.75       # доля закрытых обязательных полей у каждого кандидата
    max_turns_without_progress: int = 8    # → CONTACT
    cta_cooldown_turns: int = 3            # повторный призыв после отказа
    max_asks_per_slot: int = 2
    rerank_floor: float | None = None      # τ: ниже — уточняем или зовём специалиста (подбирается на eval)
    gating_bonus: float = 1.0              # бонусы в value(slot) при выборе next_slot
    discriminating_bonus: float = 0.5


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    dialog_from: DialogState
    dialog_to: DialogState
    candidates: tuple[Product, ...]        # активные блоки анкеты
    leader: Product | None
    need_facts: bool                       # идти ли в базу знаний (ровно один поиск за ход)
    next_slot: str | None                  # что спросить; None — не спрашиваем
    offer_contact: bool                    # показать призыв к заявке
    pinned_sections: tuple[str, ...] = ()  # разделы по id без поиска: карточка лидера, 03-calculation…
    reason: str = ""                       # для отладки: почему такой переход
