"""Скоринг продуктов: правила, их срабатывания и итоговая таблица.

score[p] = prior[p | segment]
         + Σ rule.weight   по правилам, сработавшим на ТЕКУЩИХ слотах (пересчёт с нуля)
         + Σ λ^(T − t) · soft[p, t]
P = softmax(score / T) по неисключённым продуктам.

Сами правила (≈30, со ссылками на базу) — в rules.py, по таблице из docs/spec.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from energytool.domain.products import Product
from energytool.domain.state import ConversationState

EXCLUDE = float("-inf")   # правило «не подходит» исключает продукт


@dataclass(frozen=True, slots=True)
class Rule:
    id: str                                        # "OREM_GATE"
    product: Product
    when: Callable[[ConversationState], bool]      # чистая функция от состояния
    weight: float                                  # > 0, < 0 или EXCLUDE
    explain: str                                   # «мощность {max_power_kw} кВт выше порога 750 кВт»
    kb_source: str                                 # «20-products/21-orem › Порог доступа…»
    flag: bool = False                             # только флаг в бриф, на балл не влияет


@dataclass(frozen=True, slots=True)
class RuleHit:
    rule_id: str
    product: Product
    weight: float
    text: str                                      # explain с подставленными значениями
    kb_source: str
    flag: bool = False


@dataclass(frozen=True, slots=True)
class ScoringParams:
    temperature: float = 2.0
    decay: float = 0.7                             # λ для мягких сигналов
    mention_weight: float = 2.0
    retrieval_mass_weight: float = 1.0


@dataclass(frozen=True, slots=True)
class ProductScore:
    product: Product
    raw: float
    probability: float                             # 0, если исключён
    excluded: bool
    hits: tuple[RuleHit, ...] = ()


@dataclass(frozen=True, slots=True)
class ScoreBoard:
    turn: int
    scores: tuple[ProductScore, ...]
    trace: tuple[RuleHit, ...] = field(default=())

    def ranked(self) -> tuple[ProductScore, ...]:
        return tuple(sorted((s for s in self.scores if not s.excluded),
                            key=lambda s: s.probability, reverse=True))

    def probability(self, product: Product) -> float:
        return next((s.probability for s in self.scores if s.product is product), 0.0)

    def excluded(self) -> tuple[ProductScore, ...]:
        return tuple(s for s in self.scores if s.excluded)
