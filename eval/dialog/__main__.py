"""Стенд диалогов: `uv run --env-file .env python -m eval.dialog [--personas ...] [--turns 8]`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from energytool.application.build_index import KnowledgeIndex
from energytool.application.handle_turn import HandleTurn
from energytool.application.trace import TurnTrace
from energytool.bootstrap import Container, build
from energytool.domain.knowledge import BudgetSnippet
from energytool.domain.slots import SlotScalar, SlotStatus
from energytool.domain.state import ConversationState, DialogState
from energytool.infrastructure.llm.gateway import LlmGatewayError
from eval.common import KNOWLEDGE_BASE, results_path
from eval.dialog.personas import PERSONAS, Persona

CLIENT_SYSTEM = """\
Ты играешь клиента в чате сайта Energytool. Легенда: {legend}
Правила: отвечай коротко (1–2 предложения), как обычный человек, без терминов, если их \
не знаешь. Раскрывай факты из легенды только на заданный вопрос, можешь добавить одну \
деталь. На вопросы про {unknown} отвечай «не знаю». Если консультант предлагает оставить \
контакты или заявку — соглашайся и напиши: {contact}. Не выдумывай фактов вне легенды."""


class MemorySessions:
    def __init__(self) -> None:
        self.states: dict[str, ConversationState] = {}

    def load(self, session_id: str) -> ConversationState | None:
        return self.states.get(session_id)

    def save(self, state: ConversationState, trace: TurnTrace) -> None:
        self.states[state.session_id] = state


@dataclass(slots=True)
class DialogResult:
    persona: Persona
    turns: int = 0
    ready_turn: int | None = None
    submitted: bool = False
    leader: str | None = None
    excluded: list[str] = field(default_factory=list)
    filled: dict[str, SlotScalar | None] = field(default_factory=dict)
    transcript: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def correct(self) -> list[str]:
        return [k for k, v in self.persona.expected.items() if _matches(self.filled.get(k), v)]

    def wrong(self) -> list[str]:
        return [
            k
            for k, v in self.persona.expected.items()
            if k in self.filled and self.filled[k] is not None and not _matches(self.filled[k], v)
        ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.dialog")
    parser.add_argument("--personas", nargs="+", default=[p.id for p in PERSONAS])
    parser.add_argument("--turns", type=int, default=8)
    args = parser.parse_args(argv)

    container = build()
    container.gateway.calibrate_thinking()
    index = container.build_index(KNOWLEDGE_BASE)
    personas = [p for p in PERSONAS if p.id in args.personas]
    with ThreadPoolExecutor(max_workers=container.settings.max_workers) as pool:
        results = list(pool.map(lambda p: simulate(container, index, p, args.turns), personas))
    report = render(results)
    print(report.split("\n## Диалоги")[0])
    path = results_path("dialog")
    path.write_text(report, encoding="utf-8")
    print(f"отчёт: {path}")
    container.close()
    return 0


def simulate(
    container: Container, index: KnowledgeIndex, persona: Persona, max_turns: int
) -> DialogResult:
    sessions = MemorySessions()
    retrieve = container.retriever(index)
    budget = BudgetSnippet(top_k=container.settings.top_k, fallback_k=1)
    turn = HandleTurn(
        sessions=sessions,
        extractor=container.extractor(),
        qualifier=container.qualifier(retrieve, budget),
        writer=container.reply_writer(),
        telemetry=container.telemetry,
        submit_lead=lambda step: "eval-lead",
    )
    result = DialogResult(persona)
    message = persona.opening
    client_history: list[dict[str, str]] = []
    for n in range(1, max_turns + 1):
        response = turn(persona.id, message)
        result.turns = n
        result.transcript += [f"👤 {message}", f"🤖 {response.reply.text}"]
        if response.dialog is DialogState.READY and result.ready_turn is None:
            result.ready_turn = n
        if response.dialog is DialogState.SUBMITTED:
            result.submitted = True
            break
        client_history += [
            {"role": "assistant", "content": message},
            {"role": "user", "content": response.reply.text},
        ]
        try:
            message = client_reply(container, persona, client_history)
        except LlmGatewayError as exc:
            result.errors.append(str(exc))
            break
    state = sessions.states[persona.id]
    result.leader = (
        state.leader_history[-1].value
        if state.leader_history and state.leader_history[-1]
        else None
    )
    result.filled = {k: v.value for k, v in state.slots.items() if v.status is SlotStatus.FILLED}
    return result


def client_reply(container: Container, persona: Persona, history: list[dict[str, str]]) -> str:
    system = CLIENT_SYSTEM.format(
        legend=persona.legend,
        unknown=", ".join(persona.unknown) or "то, чего нет в легенде",
        contact=persona.contact,
    )
    result = container.gateway.chat(
        [{"role": "system", "content": system}, *history],  # type: ignore[list-item]
        temperature=0.5,
        max_tokens=200,
    )
    return result.text or "Не знаю."


def _matches(actual: SlotScalar | None, expected: SlotScalar) -> bool:
    if actual is None:
        return False
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual == expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return abs(actual - expected) <= 0.1 * abs(expected)
    return str(expected).lower() in str(actual).lower()


def render(results: list[DialogResult]) -> str:
    lines = [
        "# Dialog eval",
        "",
        "| persona | turns | ready@ | submitted | leader (ждали) | recall | wrong |",
        "|---|---|---|---|---|---|---|",
    ]
    total_expected = total_correct = total_wrong = 0
    for r in results:
        correct, wrong = r.correct(), r.wrong()
        total_expected += len(r.persona.expected)
        total_correct += len(correct)
        total_wrong += len(wrong)
        leader_ok = "✅" if r.leader == r.persona.expected_leader else "❌"
        lines.append(
            f"| {r.persona.id} | {r.turns} | {r.ready_turn or '—'} | "
            f"{'да' if r.submitted else 'нет'} | "
            f"{leader_ok} {r.leader} ({r.persona.expected_leader}) | "
            f"{len(correct)}/{len(r.persona.expected)} | {', '.join(wrong) or '—'} |"
        )
    leaders = sum(r.leader == r.persona.expected_leader for r in results)
    lines += [
        "",
        f"Итого: полнота анкеты {total_correct}/{total_expected} "
        f"({total_correct / max(total_expected, 1):.0%}), ошибочных полей {total_wrong}, "
        f"верный лидер {leaders}/{len(results)}, "
        f"заявок {sum(r.submitted for r in results)}/{len(results)}.",
        "",
        "## Диалоги",
        "",
    ]
    for r in results:
        lines += [
            f"### {r.persona.id}",
            "",
            *[f"- {t}" for t in r.transcript],
            f"- извлечено: {r.filled}",
            "",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
