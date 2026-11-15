"""ReplyWriter: ReplyPlan → текст ответа клиенту (spec §8.3).

- template_only или LLM недоступна → шаблон из лучшего фрагмента без LLM;
- иначе chat с системным промптом консультанта, few-shot и фрагментами;
- пост-обработка кодом: одна строка без markdown, лимит слов по границе предложения,
  предупреждение в лог, если в ответе есть числа, которых нет во фрагментах и вопросе.

Ответ никогда не бывает пустым.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from energytool.domain.knowledge import Evidence
from energytool.domain.reply import Reply, ReplyPlan, StyleExample
from energytool.domain.state import Message, Role
from energytool.infrastructure.llm.gateway import ChatMessage, LlmGateway, LlmGatewayError
from energytool.infrastructure.llm.prompts import (
    CONTACT_CTA,
    FALLBACK_NO_EVIDENCE,
    FALLBACK_TAIL,
    RESPONDER_ACK,
    RESPONDER_ASK,
    RESPONDER_ASK_WHY,
    RESPONDER_BEST_FIRST,
    RESPONDER_BEST_LAST,
    RESPONDER_CONTEXT,
    RESPONDER_EXAMPLES,
    RESPONDER_HINTS,
    RESPONDER_LIMITS,
    RESPONDER_QUESTION,
    RESPONDER_STYLE_EXAMPLE,
    RESPONDER_STYLE_EXAMPLES,
    RESPONDER_SYSTEM,
    RESPONDER_TOPIC,
    RESPONDER_USER,
    RESPONDER_USER_RULES_FIRST,
    RESPONDER_VERSION,
    RESPONDER_WEAK_EVIDENCE,
    TEMPLATE_ACK_EMPTY,
)

logger = logging.getLogger(__name__)

HARD_WORD_LIMIT = 150  # лимит кейса
EVIDENCE_CHARS = 2400  # на один фрагмент в промпте
HISTORY_TURNS = 6
REPLY_MAX_TOKENS = 450
# своя температура: TEMPERATURE платформы подобрана под учебный пример и может быть выше
REPLY_TEMPERATURE = 0.1
REFUSAL_EXAMPLES = 2  # последние встроенные примеры: «нет данных» и «вне темы»

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_MARKDOWN = re.compile(r"(\*\*|__|`|^#+\s*|^\s*[-*•]\s+|^\s*\d+[.)]\s+)", re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class LlmReplyWriter:
    def __init__(
        self,
        gateway: LlmGateway,
        max_tokens: int = REPLY_MAX_TOKENS,
        *,
        rules_first: bool = False,
        best_last: bool = False,
        style_examples: Sequence[StyleExample] = (),
        style_holdout: bool = False,
    ) -> None:
        self._gateway = gateway
        self._max_tokens = max_tokens
        self._layout = PromptLayout(rules_first=rules_first, best_last=best_last)
        self._style_examples = tuple(style_examples)
        self._style_holdout = style_holdout

    def write(self, plan: ReplyPlan, history: Sequence[Message]) -> Reply:
        evidence_ids = tuple(e.chunk.id for e in plan.evidence)
        if not plan.answer_question:
            return Reply(text=plan_template(plan), used_llm=False, evidence_ids=evidence_ids)
        if plan.template_only:
            return Reply(
                text=_with_cta(template_reply(plan), plan),
                used_llm=False,
                evidence_ids=evidence_ids,
            )
        messages = build_messages(plan, history, self._layout, self._examples_for(plan))
        try:
            result = self._gateway.chat(
                messages, temperature=REPLY_TEMPERATURE, max_tokens=self._max_tokens
            )
        except LlmGatewayError as exc:
            logger.error("responder degraded to template: %s", exc)
            return Reply(
                text=_with_cta(template_reply(plan), plan),
                used_llm=False,
                evidence_ids=evidence_ids,
            )
        text = clean_reply(result.text, HARD_WORD_LIMIT)
        if not text:
            logger.warning("responder returned empty text, using template")
            return Reply(
                text=_with_cta(template_reply(plan), plan),
                used_llm=False,
                evidence_ids=evidence_ids,
            )
        unsupported = unsupported_numbers(text, plan)
        logger.info(
            "reply written",
            extra={
                "data": {
                    "prompt": RESPONDER_VERSION,
                    "words": len(text.split()),
                    "finish": result.finish_reason,
                    "ask": plan.ask.slot.id if plan.ask else None,
                    "offer_contact": plan.offer_contact,
                    "unsupported_numbers": unsupported,
                }
            },
        )
        return Reply(text=_with_cta(text, plan), used_llm=True, evidence_ids=evidence_ids)

    def _examples_for(self, plan: ReplyPlan) -> tuple[StyleExample, ...]:
        if not self._style_holdout:
            return self._style_examples
        question = plan.answer_question or ""
        return tuple(e for e in self._style_examples if not e.is_about(question))


def plan_template(plan: ReplyPlan) -> str:
    """Ход без вопроса клиента (он отвечал на анкету): подтверждение + следующий вопрос
    анкеты + призыв к заявке — без LLM (spec §8.3)."""
    parts = [_sentence(plan.acknowledge[0]) if plan.acknowledge else TEMPLATE_ACK_EMPTY]
    if plan.ask is not None:
        parts.append(plan.ask.ask)
    text = " ".join(parts)
    return _with_cta(text, plan) if plan.offer_contact or plan.ask is None else text


def _with_cta(text: str, plan: ReplyPlan) -> str:
    if not plan.offer_contact or CONTACT_CTA in text:
        return text
    return f"{text} {CONTACT_CTA}"


def _sentence(text: str) -> str:
    text = text.strip()
    return (
        (text[:1].upper() + text[1:] + ("" if text.endswith((".", "!", "?")) else "."))
        if text
        else ""
    )


def guidance(plan: ReplyPlan) -> str:
    extra = RESPONDER_WEAK_EVIDENCE if plan.weak_evidence else ""
    if plan.acknowledge:
        extra += RESPONDER_ACK.format(facts="; ".join(plan.acknowledge))
    if plan.ask is not None:
        why = RESPONDER_ASK_WHY.format(why=plan.ask.why) if plan.ask.why else ""
        extra += RESPONDER_ASK.format(ask=plan.ask.ask, why=why)
    return extra


@dataclass(frozen=True, slots=True)
class PromptLayout:
    """Порядок блоков в промпте: что стоит ближе к вопросу клиента."""

    rules_first: bool = False  # ограничения и подсказки перед фактами
    best_last: bool = False  # самый близкий к вопросу фрагмент — последним

    @property
    def user_template(self) -> str:
        return RESPONDER_USER_RULES_FIRST if self.rules_first else RESPONDER_USER

    @property
    def system(self) -> str:
        order = RESPONDER_BEST_LAST if self.best_last else RESPONDER_BEST_FIRST
        return RESPONDER_SYSTEM.format(evidence_order=order)


DEFAULT_LAYOUT = PromptLayout()


def build_messages(
    plan: ReplyPlan,
    history: Sequence[Message],
    layout: PromptLayout = DEFAULT_LAYOUT,
    style_examples: Sequence[StyleExample] = (),
) -> list[ChatMessage]:
    template = layout.user_template
    system = layout.system + format_style_examples(style_examples)
    messages: list[ChatMessage] = [{"role": "system", "content": system}]
    # с образцами остаются только встроенные примеры отказа: в образцах отказов нет
    builtin = RESPONDER_EXAMPLES[-REFUSAL_EXAMPLES:] if plan.shots else RESPONDER_EXAMPLES
    for question, evidence, answer in builtin:
        messages.append(
            {
                "role": "user",
                "content": template.format(evidence=evidence, limits="", question=question),
            }
        )
        messages.append({"role": "assistant", "content": answer})
    for shot in plan.shots:
        asked = shot.history[-1].text if shot.history else shot.question
        messages.append(
            {"role": "user", "content": _user_content(shot.evidence, shot.history, asked, layout)}
        )
        messages.append({"role": "assistant", "content": shot.answer})
    asked = (plan.answer_question or "") + guidance(plan)
    messages.append(
        {"role": "user", "content": _user_content(plan.evidence, history, asked, layout)}
    )
    return messages


def _user_content(
    evidence: Sequence[Evidence], history: Sequence[Message], asked: str, layout: PromptLayout
) -> str:
    facts = [e for e in evidence if e.origin not in ("pinned", "hint", "context", "topic")]
    topic = [e for e in evidence if e.origin == "topic"]
    if layout.best_last:
        facts.reverse()
    context = [e for e in evidence if e.origin == "context"]
    limits = [e for e in evidence if e.origin == "pinned"]
    hints = [e for e in evidence if e.origin == "hint"]
    return layout.user_template.format(
        evidence=format_evidence(facts)
        + (RESPONDER_TOPIC.format(topic=format_limits(topic)) if topic else "")
        + (RESPONDER_CONTEXT.format(context=format_limits(context)) if context else ""),
        limits=(RESPONDER_LIMITS.format(limits=format_limits(limits)) if limits else "")
        + (RESPONDER_HINTS.format(hints=format_limits(hints)) if hints else ""),
        question=RESPONDER_QUESTION.format(history=format_history(history), question=asked),
    )


def format_style_examples(examples: Sequence[StyleExample]) -> str:
    if not examples:
        return ""
    pairs = "\n\n".join(
        RESPONDER_STYLE_EXAMPLE.format(question=e.question, answer=e.answer) for e in examples
    )
    return RESPONDER_STYLE_EXAMPLES.format(examples=pairs)


def format_evidence(evidence: Sequence[Evidence]) -> str:
    if not evidence:
        return "(ничего не найдено)"
    return "\n\n".join(
        f"[{i}] {e.chunk.path}\n{_body(e)[:EVIDENCE_CHARS]}" for i, e in enumerate(evidence, 1)
    )


def format_limits(limits: Sequence[Evidence]) -> str:
    return "\n\n".join(f"{e.chunk.path}\n{_body(e)[:EVIDENCE_CHARS]}" for e in limits)


def format_history(history: Sequence[Message]) -> str:
    """Предыдущие реплики без последней (она идёт как «Вопрос клиента»)."""
    earlier = list(history[:-1])[-HISTORY_TURNS:]
    if not earlier:
        return ""
    names = {Role.USER: "Клиент", Role.ASSISTANT: "Консультант"}
    lines = "\n".join(f"{names[m.role]}: {m.text}" for m in earlier)
    return f"История диалога:\n{lines}\n\n"


def clean_reply(text: str, word_limit: int) -> str:
    """Одна строка без markdown; длинный ответ режется по последнему целому предложению."""
    text = _MARKDOWN.sub("", text)
    text = " ".join(text.split()).strip().strip('"«»')
    words = text.split()
    if len(words) <= word_limit:
        return text
    kept: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if len(" ".join([*kept, sentence]).split()) > word_limit:
            break
        kept.append(sentence)
    return " ".join(kept) if kept else " ".join(words[:word_limit]) + "…"


def unsupported_numbers(text: str, plan: ReplyPlan) -> list[str]:
    """Числа ответа, которых нет ни во фрагментах, ни в вопросе: кандидаты в выдумки."""
    allowed_text = " ".join([plan.answer_question or "", *(e.chunk.text for e in plan.evidence)])
    allowed = {_normalize_number(n) for n in _NUMBER.findall(allowed_text)}
    return [n for n in _NUMBER.findall(text) if _normalize_number(n) not in allowed]


def template_reply(plan: ReplyPlan) -> str:
    """Ответ без LLM: первые предложения лучшего фрагмента + просьба уточнить."""
    ranked = [e for e in plan.evidence if e.origin in ("ranked", "by_id", "pinned")]
    if not ranked:
        return FALLBACK_NO_EVIDENCE
    prose = _prose(_body(ranked[0]))
    if not prose:
        return FALLBACK_NO_EVIDENCE
    lead = " ".join(_SENTENCE_END.split(prose)[:2])
    return clean_reply(f"{lead} {FALLBACK_TAIL}", HARD_WORD_LIMIT)


def _body(evidence: Evidence) -> str:
    """Текст раздела без первой строки-пути (путь уже в заголовке фрагмента)."""
    return evidence.chunk.text.split("\n", 1)[-1].strip()


def _prose(body: str) -> str:
    """Связный текст раздела: без таблиц, списков и коротких строк-заголовков."""
    lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip()
        and not line.lstrip().startswith(("|", "-", "*", "#", "+"))
        and not re.match(r"^\s{2,}\S", line)
    ]
    return _MARKDOWN.sub("", " ".join(lines)).strip()


def _normalize_number(raw: str) -> str:
    return raw.replace(",", ".")
