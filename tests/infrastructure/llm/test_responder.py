import json
from dataclasses import replace

import httpx

from energytool.domain.knowledge import Evidence
from energytool.domain.products import Product
from energytool.domain.reply import ReplyPlan, StyleExample, StyleShot
from energytool.domain.state import Message, Role
from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.gateway import LlmGateway
from energytool.infrastructure.llm.prompts import FALLBACK_NO_EVIDENCE, FALLBACK_TAIL
from energytool.infrastructure.llm.responder import (
    REFUSAL_EXAMPLES,
    LlmReplyWriter,
    PromptLayout,
    build_messages,
    clean_reply,
    template_reply,
    unsupported_numbers,
)
from tests.application.fakes import chunk

EVIDENCE = (
    Evidence(
        chunk(
            "orem#порог",
            "ОРЭМ › Порог\nПорог доступа — 750 кВт. Ниже нельзя.\n| таблица |",
            Product.OREM,
        ),
        0.9,
        "ranked",
    ),
)


def plan(question: str = "Нам подходит опт?", template_only: bool = False) -> ReplyPlan:
    return ReplyPlan(
        answer_question=question,
        evidence=EVIDENCE,
        ask=None,
        offer_contact=False,
        template_only=template_only,
    )


def writer(content: str | None, status: int = 200) -> tuple[LlmReplyWriter, list[dict]]:  # type: ignore[type-arg]
    seen: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    settings = Settings.from_env(
        {"LLM_BASE_URL": "http://p", "LLM_API_KEY": "k", "LLM_MAX_RETRIES": "0"}
    )
    gateway = LlmGateway(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    return LlmReplyWriter(gateway), seen


def test_clean_reply_strips_markdown_and_newlines() -> None:
    assert clean_reply("**Да.**\n- пункт один\n- пункт два", 150) == "Да. пункт один пункт два"


def test_clean_reply_cuts_by_sentence() -> None:
    text = "Первое предложение тут. Второе предложение тоже. Третье."
    assert clean_reply(text, 6) == "Первое предложение тут. Второе предложение тоже."


def test_unsupported_numbers_detects_invented_figures() -> None:
    assert unsupported_numbers("Порог 750 кВт, экономия 30%", plan()) == ["30"]


def test_template_uses_prose_of_best_evidence() -> None:
    text = template_reply(plan())
    assert text.startswith("Порог доступа — 750 кВт. Ниже нельзя.")
    assert text.endswith(FALLBACK_TAIL)
    assert "|" not in text


def test_template_without_evidence() -> None:
    empty = ReplyPlan(answer_question="?", evidence=(), ask=None, offer_contact=False)
    assert template_reply(empty) == FALLBACK_NO_EVIDENCE


def test_messages_include_examples_history_and_evidence() -> None:
    history = [
        Message(Role.USER, "Мы завод", 0),
        Message(Role.ASSISTANT, "Какая мощность?", 1),
        Message(Role.USER, "Нам подходит опт?", 2),
    ]
    messages = build_messages(plan(), history)
    assert messages[0]["role"] == "system"
    last = messages[-1]["content"]
    assert "[1] orem#порог" in last
    assert "Клиент: Мы завод\nКонсультант: Какая мощность?" in last
    assert "Вопрос клиента: Нам подходит опт?" in last
    assert "Границы и ограничения" not in last


def test_messages_put_pinned_sections_into_limits_and_hints_blocks() -> None:
    base = plan()
    limit = replace(base.evidence[0], origin="pinned")
    hint = replace(base.evidence[0], origin="hint")
    with_guards = replace(base, evidence=(*base.evidence, limit, hint), weak_evidence=True)
    last = build_messages(with_guards, [])[-1]["content"]
    facts, rest = last.split("Границы и ограничения:")
    limits, hints = rest.split("Что можно уточнить у клиента:")
    assert "[1] orem#порог" in facts and "[2]" not in facts
    assert "orem#порог" in limits
    assert "orem#порог" in hints
    assert "Поиск не нашёл" in hints


def test_writer_uses_llm_and_cleans() -> None:
    reply_writer, seen = writer("Да,\nпорог 750 кВт.")
    reply = reply_writer.write(plan(), [])
    assert reply.text == "Да, порог 750 кВт."
    assert reply.used_llm is True
    assert reply.evidence_ids == ("orem#порог",)
    assert len(seen) == 1


def test_writer_falls_back_to_template_on_llm_error_and_empty() -> None:
    for reply_writer, _ in (writer(None, status=503), writer("   ")):
        reply = reply_writer.write(plan(), [])
        assert reply.used_llm is False
        assert reply.text.startswith("Порог доступа")


def test_template_only_does_not_call_llm() -> None:
    reply_writer, seen = writer("не должно")
    assert reply_writer.write(plan(template_only=True), []).used_llm is False
    assert seen == []


def test_default_prompt_keeps_best_first_order_and_no_style_block() -> None:
    system = build_messages(plan(), [])[0]["content"]
    assert "по убыванию близости к вопросу" in system
    assert "{evidence_order}" not in system
    assert "Образцы ответов" not in system


def test_layout_moves_limits_before_facts_and_reverses_evidence() -> None:
    base = plan()
    second = replace(base.evidence[0], chunk=chunk("orem#цена", "цена", Product.OREM))
    limit = replace(base.evidence[0], origin="pinned")
    messages = build_messages(
        replace(base, evidence=(*base.evidence, second, limit)),
        [],
        PromptLayout(rules_first=True, best_last=True),
    )
    last = messages[-1]["content"]
    assert last.index("Границы и ограничения:") < last.index("Фрагменты базы знаний:")
    assert last.index("[1] orem#цена") < last.index("[2] orem#порог")
    assert "по возрастанию близости к вопросу" in messages[0]["content"]


def test_topic_and_context_sections_get_their_own_blocks() -> None:
    base = plan()
    topic = replace(base.evidence[0], chunk=chunk("askue#цеха", "учёт", Product.METERING))
    near = replace(base.evidence[0], chunk=chunk("orem#рядом", "сосед", Product.OREM))
    evidence = (*base.evidence, replace(topic, origin="topic"), replace(near, origin="context"))
    last = build_messages(replace(base, evidence=evidence), [])[-1]["content"]
    facts, rest = last.split("Разделы по теме диалога")
    assert "askue#цеха" not in facts and "[2]" not in facts
    assert rest.index("askue#цеха") < rest.index("Соседние разделы") < rest.index("orem#рядом")


def test_style_examples_go_to_system_prompt_as_block() -> None:
    examples = (StyleExample("Панели на фасад ставите?", "Да, ставим."),)
    system = build_messages(plan(), [], style_examples=examples)[0]["content"]
    assert "Вопрос клиента: Панели на фасад ставите?\nОтвет консультанта: Да, ставим." in system


def test_holdout_hides_style_example_for_the_same_question() -> None:
    examples = (
        StyleExample("Пользователь: Мы завод\nПользователь: Нам подходит опт?", "Проверим."),
        StyleExample("Панели на фасад ставите?", "Да, ставим."),
    )
    reply_writer, seen = writer("Ответ.")
    held_out = LlmReplyWriter(reply_writer._gateway, style_examples=examples, style_holdout=True)
    held_out.write(plan(), [])
    system = seen[0]["messages"][0]["content"]
    assert "Да, ставим." in system and "Проверим." not in system


def test_style_shots_replace_builtin_examples_except_refusals() -> None:
    shot = StyleShot(
        question="Панели на фасад ставите?",
        history=(Message(Role.USER, "Панели на фасад ставите?", 0),),
        evidence=EVIDENCE,
        answer="Да, ставим.",
    )
    messages = build_messages(replace(plan(), shots=(shot,)), [])
    assert len(messages) == 1 + 2 * REFUSAL_EXAMPLES + 2 + 1
    assert messages[-2] == {"role": "assistant", "content": "Да, ставим."}
    assert "Вопрос клиента: Панели на фасад ставите?" in messages[-3]["content"]
    assert "[1] orem#порог" in messages[-3]["content"]
