"""Product Scorer (spec §5.3): объяснимый балл продуктов на текущем ходе.

    score[p] = prior[p | segment] + Σ weight сработавших правил + Σ λ^(T − t) · soft[p, t]
    excluded[p] = сработало правило с weight = EXCLUDE
    P = softmax(score / T) по неисключённым

Жёсткие факты пересчитываются с нуля, затухают только мягкие сигналы (упоминания,
масса ретрива). Чистая функция, без IO.
"""

from __future__ import annotations

import math
import string
from collections.abc import Mapping, Sequence

from energytool.domain.products import ALL_PRODUCTS, Product, Segment
from energytool.domain.scoring import (
    EXCLUDE,
    ProductScore,
    Rule,
    RuleHit,
    ScoreBoard,
    ScoringParams,
)
from energytool.domain.state import ConversationState, SoftSignal


def score(
    state: ConversationState,
    rules: Sequence[Rule],
    priors: Mapping[Segment, Mapping[Product, float]],
    params: ScoringParams,
) -> ScoreBoard:
    hits = [_hit(rule, state) for rule in rules if rule.when(state)]
    segment = _segment(state)
    prior = priors.get(segment, {}) if segment is not None else {}
    raw: dict[Product, float] = {}
    excluded: dict[Product, bool] = {}
    for product in ALL_PRODUCTS:
        product_hits = [h for h in hits if h.product is product]
        excluded[product] = any(h.weight == EXCLUDE for h in product_hits)
        raw[product] = (
            prior.get(product, 0.0)
            + sum(h.weight for h in product_hits if not h.flag and h.weight != EXCLUDE)
            + _soft(state.soft_signals, product, state.turn, params)
        )
    probabilities = _softmax({p: raw[p] for p in ALL_PRODUCTS if not excluded[p]}, params)
    scores = tuple(
        ProductScore(
            product=p,
            raw=round(raw[p], 4),
            probability=probabilities.get(p, 0.0),
            excluded=excluded[p],
            hits=tuple(h for h in hits if h.product is p),
        )
        for p in ALL_PRODUCTS
    )
    return ScoreBoard(turn=state.turn, scores=scores, trace=tuple(hits))


def _hit(rule: Rule, state: ConversationState) -> RuleHit:
    return RuleHit(
        rule_id=rule.id,
        product=rule.product,
        weight=rule.weight,
        text=_render(rule.explain, state),
        kb_source=rule.kb_source,
        flag=rule.flag,
    )


def _render(template: str, state: ConversationState) -> str:
    """Подстановка значений слотов в текст правила; неизвестные поля остаются как есть."""
    fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    values = {name: _format_value(state.value(name)) for name in fields}
    try:
        return template.format(**values)
    except (KeyError, ValueError):
        return template


def _format_value(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}".replace(",", " ")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:,}".replace(",", " ")
    return "?" if value is None else str(value)


def _segment(state: ConversationState) -> Segment | None:
    value = state.value("segment")
    try:
        return Segment(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _soft(
    signals: Sequence[SoftSignal], product: Product, turn: int, params: ScoringParams
) -> float:
    total = 0.0
    for signal in signals:
        if signal.product is not product:
            continue
        scale = (
            params.mention_weight if signal.source == "mention" else params.retrieval_mass_weight
        )
        total += scale * signal.weight * params.decay ** max(0, turn - signal.turn)
    return total


def _softmax(raw: Mapping[Product, float], params: ScoringParams) -> dict[Product, float]:
    if not raw:
        return {}
    peak = max(raw.values())
    exp = {p: math.exp((v - peak) / params.temperature) for p, v in raw.items()}
    total = sum(exp.values())
    return {p: round(v / total, 4) for p, v in exp.items()}
