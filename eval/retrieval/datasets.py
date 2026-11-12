"""Размеченные наборы для eval ретрива: запрос → ожидаемые разделы.

- faq        — заголовки-вопросы FAQ базы, метка — свой раздел. Лёгкий набор: текст
               заголовка входит в раздел, поэтому это проверка «не сломали очевидное»;
- paraphrase — те же вопросы, перефразированные Qwen словами клиента (кэш в
               eval/data/faq_paraphrases.json). Честная проверка ретрива;
- demo       — 18 демо-вопросов, разметка руками по фактам эталонных ответов
               (eval/data/demo_labels.json).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from energytool.application.knowledge_request import query_from_history
from energytool.application.transcript import parse_transcript
from energytool.domain.knowledge import (
    BudgetSnippet,
    Chunk,
    KnowledgeQuery,
    SectionKind,
    TextSnippet,
)
from energytool.infrastructure.llm.gateway import LlmGateway, LlmGatewayError
from eval.common import EVAL_DATA, load_demo, load_json, save_json

logger = logging.getLogger(__name__)

PARAPHRASES = EVAL_DATA / "faq_paraphrases.json"
DEMO_LABELS = EVAL_DATA / "demo_labels.json"
_NOT_QUESTIONS = ("Следующий шаг", "Частые вопросы")


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    set: str
    text: str
    query: KnowledgeQuery
    expected: frozenset[str]


def faq_chunks(chunks: Sequence[Chunk]) -> list[Chunk]:
    """Разделы, чей последний заголовок — вопрос клиента: FAQ продуктов, 17-faq,
    вопросы третьего уровня в 26-general."""
    selected = []
    for chunk in chunks:
        last = chunk.path.rsplit(" › ", 1)[-1]
        if last.startswith(_NOT_QUESTIONS):
            continue
        is_general_question = chunk.file.endswith("26-general.md") and chunk.path.count(" › ") == 2
        if chunk.kind is SectionKind.FAQ or is_general_question:
            selected.append(chunk)
    return selected


def _question(chunk: Chunk) -> str:
    return chunk.path.rsplit(" › ", 1)[-1]


def faq_cases(chunks: Sequence[Chunk], budget: BudgetSnippet) -> list[Case]:
    return [
        Case(c.id, "faq", _question(c), (TextSnippet(_question(c)), budget), frozenset({c.id}))
        for c in faq_chunks(chunks)
    ]


def paraphrase_cases(
    chunks: Sequence[Chunk], budget: BudgetSnippet, gateway: LlmGateway | None
) -> list[Case]:
    cache: dict[str, str] = load_json(PARAPHRASES, {})
    cases = []
    for chunk in faq_chunks(chunks):
        if chunk.id not in cache and gateway is not None:
            paraphrase = _paraphrase(gateway, _question(chunk))
            if paraphrase:
                cache[chunk.id] = paraphrase
                save_json(PARAPHRASES, cache)
        if chunk.id in cache:
            text = cache[chunk.id]
            cases.append(
                Case(
                    chunk.id, "paraphrase", text, (TextSnippet(text), budget), frozenset({chunk.id})
                )
            )
    return cases


_PARAPHRASE_PROMPT = (
    "Перепиши вопрос так, как его написал бы в чат сайта клиент-предприниматель, "
    "не разбирающийся в энергетике. Добавь короткую деталь своей ситуации, замени "
    "ключевые термины разговорными словами, где это возможно. Одна-две фразы, без "
    "кавычек и пояснений.\n\nВопрос: {question}"
)


def _paraphrase(gateway: LlmGateway, question: str) -> str:
    try:
        result = gateway.chat(
            [{"role": "user", "content": _PARAPHRASE_PROMPT.format(question=question)}],
            temperature=0.7,
            max_tokens=200,
        )
    except LlmGatewayError as exc:
        logger.warning("paraphrase failed: %s", exc)
        return ""
    return result.text.strip().strip('"«»')


def demo_cases(budget: BudgetSnippet) -> list[Case]:
    labels: dict[str, dict[str, object]] = load_json(DEMO_LABELS, {})
    cases = []
    for item in load_demo():
        label = labels.get(item.id)
        if not label:
            continue
        query = query_from_history(parse_transcript(item.question), budget)
        expected = frozenset(str(x) for x in label["expected"])  # type: ignore[attr-defined]
        cases.append(Case(item.id, "demo", item.question.splitlines()[-1], query, expected))
    return cases
