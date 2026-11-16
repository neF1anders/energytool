"""Pairwise LLM-судья по методике регламента (раздел 8)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from energytool.infrastructure.llm.gateway import LlmGateway, LlmGatewayError

logger = logging.getLogger(__name__)

JUDGE_VERSION = "judge-v1"

JUDGE_SYSTEM = """\
Ты — строгий эксперт, оцениваешь ответы чат-бота консультанта МТС Energytool \
(энергетические решения для бизнеса). Сравни два ответа на последний вопрос клиента, \
опираясь на эталонный ответ.

Критерии по убыванию важности:
1. Фактическая точность и согласие с эталоном; выдуманные факты и цифры — серьёзный минус.
2. Полнота: есть ли ключевые факты и выводы эталона.
3. Полезность для клиента: прямой ответ, понятное объяснение, уместный уточняющий вопрос.
4. Краткость и ясность (ориентир — до 150 слов).
Ответ «нет информации» при том, что эталон отвечает по существу, — серьёзный минус.

Верни только JSON: {"reason": "<1-2 предложения>", \
"verdict": "<A_much_better|A_better|tie|B_better|B_much_better>"}"""

# Второй промпт — нейтральная формулировка «какой ответ ближе к эталону»: настоящий промпт
# платформы неизвестен, поэтому вариант считается лучше, только если выигрывает у обоих.
JUDGE_SYSTEM_CLOSENESS = """\
Ты оцениваешь качество ответов ассистента. Даны вопрос клиента, эталонный ответ и два \
ответа: A и B. Определи, какой из ответов лучше соответствует эталонному по смыслу: \
те же выводы и ключевые факты, нет противоречий эталону и лишних непроверяемых \
утверждений, такой же следующий шаг для клиента (уточняющий вопрос, оговорка или отказ).

Верни только JSON: {"reason": "<1-2 предложения>", \
"verdict": "<A_much_better|A_better|tie|B_better|B_much_better>"}"""

JUDGE_PROMPTS = {"v1": JUDGE_SYSTEM, "v2": JUDGE_SYSTEM_CLOSENESS}

JUDGE_USER = """\
Вопрос клиента (может включать историю диалога):
{question}

Эталонный ответ:
{golden}

Ответ A:
{a}

Ответ B:
{b}"""

_VERDICT = re.compile(r"(A_much_better|A_better|tie|B_better|B_much_better)")
# балл ответа B относительно A
_SCORE_FOR_B = {
    "A_much_better": 0.0,
    "A_better": 0.25,
    "tie": 0.5,
    "B_better": 0.75,
    "B_much_better": 1.0,
}


@dataclass(frozen=True, slots=True)
class Judgement:
    score: float  # для ответа команды, 0..1
    reason: str
    raw: str


@dataclass(frozen=True, slots=True)
class QuestionScore:
    qid: str
    score: float | None  # None — судья не смог (исключается, как у платформы)
    rounds: tuple[Judgement | None, Judgement | None]


def judge_pair(
    gateway: LlmGateway,
    question: str,
    golden: str,
    team: str,
    baseline: str,
    system: str = JUDGE_SYSTEM,
) -> tuple[Judgement | None, Judgement | None]:
    if team.strip() == baseline.strip():
        same = Judgement(score=0.5, reason="ответы совпадают", raw="tie")
        return same, same
    first = _round(gateway, system, question, golden, a=baseline, b=team, team_is_b=True)
    second = _round(gateway, system, question, golden, a=team, b=baseline, team_is_b=False)
    return first, second


def combine(qid: str, rounds: tuple[Judgement | None, Judgement | None]) -> QuestionScore:
    if rounds[0] is None or rounds[1] is None:
        return QuestionScore(qid, None, rounds)
    return QuestionScore(qid, (rounds[0].score + rounds[1].score) / 2, rounds)


def _round(
    gateway: LlmGateway,
    system: str,
    question: str,
    golden: str,
    *,
    a: str,
    b: str,
    team_is_b: bool,
) -> Judgement | None:
    prompt = JUDGE_USER.format(question=question, golden=golden, a=a, b=b)
    for _ in range(2):
        try:
            result = gateway.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=300,
            )
        except LlmGatewayError as exc:
            logger.warning("judge call failed: %s", exc)
            continue
        verdict = _parse_verdict(result.text)
        if verdict is None:
            logger.warning("judge verdict not parsed: %s", result.text[:200])
            continue
        score_b = _SCORE_FOR_B[verdict]
        score = score_b if team_is_b else 1.0 - score_b
        return Judgement(score=score, reason=_parse_reason(result.text), raw=verdict)
    return None


def _parse_verdict(text: str) -> str | None:
    try:
        data = json.loads(text[text.index("{") : text.rindex("}") + 1])
        verdict = str(data.get("verdict", ""))
        if verdict in _SCORE_FOR_B:
            return verdict
    except ValueError:
        pass
    match = _VERDICT.search(text)
    return match.group(1) if match else None


def _parse_reason(text: str) -> str:
    try:
        return str(json.loads(text[text.index("{") : text.rindex("}") + 1]).get("reason", ""))
    except ValueError:
        return text[:200]


@dataclass(frozen=True, slots=True)
class Leaderboard:
    n: int
    match_rate: float
    win_rate: float
    score: float
    wins: int
    losses: int
    ties: int


def leaderboard(scores: list[QuestionScore]) -> Leaderboard:
    judged = [s.score for s in scores if s.score is not None]
    wins = sum(1 for s in judged if s > 0.5)
    losses = sum(1 for s in judged if s < 0.5)
    match_rate = sum(judged) / len(judged) if judged else 0.0
    win_rate = wins / (wins + losses) if wins + losses else 0.5
    return Leaderboard(
        n=len(judged),
        match_rate=match_rate,
        win_rate=win_rate,
        score=0.7 * match_rate + 0.3 * win_rate,
        wins=wins,
        losses=losses,
        ties=len(judged) - wins - losses,
    )
