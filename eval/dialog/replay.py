"""Повтор диалогов с заранее заданными репликами клиента:
`uv run --env-file .env python -m eval.dialog.replay [--scripts ...] [--runs 2]`.

В отличие от eval.dialog клиент не играется LLM, поэтому варианты бота сравниваются на
одинаковом входе. Метрики: лидер по ходам, на каком ходу лидером стал ожидаемый продукт,
сколько вопросов анкеты по отвергнутому продукту бот задал после отказа, вероятность
отвергнутого продукта в конце. Отчёт с ходами — в eval/data/results/.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from energytool.application.build_index import KnowledgeIndex
from energytool.application.handle_turn import HandleTurn
from energytool.bootstrap import Container, build
from energytool.domain.contract import ContractRegistry
from energytool.domain.knowledge import BudgetSnippet
from energytool.domain.products import Product
from eval.common import KNOWLEDGE_BASE, results_path
from eval.dialog.__main__ import MemorySessions

SOLAR = frozenset({Product.SOLAR_GRID, Product.SOLAR_HYBRID})


@dataclass(frozen=True, slots=True)
class Script:
    id: str
    lines: tuple[str, ...]
    expected_leader: Product
    rejected: frozenset[Product] = frozenset()  # от чего клиент отказался
    rejected_at: int | None = None  # номер реплики с отказом (с 1)


SCRIPTS: tuple[Script, ...] = (
    # ручной прогон консоли 2026-11-15: клиент хочет учёт, от солнца отказывается на 6-м ходу
    Script(
        id="bakery_leaks",
        lines=(
            "у меня на хлебзаводе в Тимашевске счета растут жестко, че делать? "
            "начальник всю плешь проел",
            "мощность 490 кВт, цена за киловатт-час растет, но плата растет куда быстрее",
            "16. Два предприятия",
            "какую станцию?",
            "у нас достаточно большая крыша, солнечная сторона, на земле места нет. Но проблема "
            "в рассчетах - у нас нет четкой систематизации в сборе даанных для счетов. Оттуда и "
            "ошибки и утечки",
            "да не хотим мы солнечную энергию, нам бы для начала понимать куда утекает "
            "электричество!",
            "вот это уже разговор! показания вручную с наших предприятий присылаются в whatsapp",
            "около 15000м2 склад, 1200 сам завод",
            "а еще никаких данных не надо?",
            "работаем только днем",
        ),
        expected_leader=Product.METERING,
        rejected=SOLAR,
        rejected_at=6,
    ),
    Script(
        id="warehouse_no_solar",
        lines=(
            "Добрый день, у нас логистический склад под Казанью, хотим навести порядок с учётом "
            "электроэнергии",
            "Солнечные панели не предлагайте, нам это не интересно. Счётчиков около 40, "
            "показания снимает электрик раз в месяц",
            "Площадка одна, кровля большая, но под панели мы её отдавать не будем",
            "Хотим видеть расход по арендаторам и зонам",
        ),
        expected_leader=Product.METERING,
        rejected=SOLAR,
        rejected_at=2,
    ),
    # контроль: вопрос про продукт — не отказ
    Script(
        id="office_asks_solar",
        lines=(
            "У нас торговый центр в Краснодаре, счета за электричество большие",
            "А солнечная станция нам точно не подойдёт? Крыша плоская, около 3000 м²",
            "Работаем днём, с 10 до 22",
        ),
        expected_leader=Product.SOLAR_GRID,
    ),
)


@dataclass(slots=True)
class TurnLog:
    n: int
    client: str
    reply: str
    leader: str | None
    top: str
    next_slot: str | None
    dialog: str


@dataclass(slots=True)
class ReplayResult:
    script: Script
    run: int
    turns: list[TurnLog] = field(default_factory=list)
    rejected_asks: int = 0  # вопросов анкеты по отвергнутому продукту после отказа
    expected_leader_turn: int | None = None
    final_rejected_probability: float = 0.0
    final_leader: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval.dialog.replay")
    parser.add_argument("--scripts", nargs="+", default=[s.id for s in SCRIPTS])
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--label", default="replay")
    args = parser.parse_args(argv)

    container = build()
    container.gateway.calibrate_thinking()
    index = container.build_index(KNOWLEDGE_BASE)
    jobs = [(s, run) for s in SCRIPTS if s.id in args.scripts for run in range(1, args.runs + 1)]
    with ThreadPoolExecutor(max_workers=container.settings.max_workers) as pool:
        results = list(pool.map(lambda job: replay(container, index, *job), jobs))
    report = render(results)
    print(report.split("\n## Ходы")[0])
    path = results_path(args.label)
    path.write_text(report, encoding="utf-8")
    print(f"отчёт: {path}")
    container.close()
    return 0


def replay(container: Container, index: KnowledgeIndex, script: Script, run: int) -> ReplayResult:
    sessions = MemorySessions()
    retrieve = container.retriever(index)
    budget = BudgetSnippet(top_k=container.settings.top_k, fallback_k=1)
    handle = HandleTurn(
        sessions=sessions,
        extractor=container.extractor(),
        qualifier=container.qualifier(retrieve, budget),
        writer=container.reply_writer(),
        telemetry=container.telemetry,
        submit_lead=lambda step: "eval-lead",
    )
    rejected_slots = product_only_slots(ContractRegistry(), script.rejected)
    result = ReplayResult(script, run)
    session_id = f"{script.id}-{run}"
    for n, line in enumerate(script.lines, 1):
        response = handle(session_id, line)
        trace = response.trace
        ranked = sorted(trace.scores_after.items(), key=lambda kv: -kv[1])[:3]
        leader = trace.dialog.get("leader") or None
        result.turns.append(
            TurnLog(
                n=n,
                client=line,
                reply=response.reply.text,
                leader=leader,
                top=", ".join(f"{p} {v:.2f}" for p, v in ranked),
                next_slot=trace.next_slot,
                dialog=response.dialog.value,
            )
        )
        if leader == script.expected_leader.value and result.expected_leader_turn is None:
            result.expected_leader_turn = n
        after_rejection = script.rejected_at is not None and n >= script.rejected_at
        if after_rejection and trace.next_slot in rejected_slots:
            result.rejected_asks += 1
        if n == len(script.lines):
            result.final_leader = leader
            result.final_rejected_probability = sum(
                trace.scores_after.get(p.value, 0.0) for p in script.rejected
            )
    return result


def product_only_slots(registry: ContractRegistry, products: frozenset[Product]) -> set[str]:
    """Поля анкеты, важные только для этих продуктов (кровля, площадка, доля дня…)."""
    if not products:
        return set()
    return {
        spec.id
        for spec in registry.slots
        if not spec.common and not spec.contact and spec.weights and set(spec.weights) <= products
    }


def render(results: list[ReplayResult]) -> str:
    lines = [
        "# Dialog replay",
        "",
        "| script | run | лидер в конце (ждали) | ожидаемый лидер с хода | "
        "вопросов по отвергнутому | P(отвергнутого) в конце |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        ok = "✅" if r.final_leader == r.script.expected_leader.value else "❌"
        rejected_p = f"{r.final_rejected_probability:.2f}" if r.script.rejected else "—"
        lines.append(
            f"| {r.script.id} | {r.run} | {ok} {r.final_leader} ({r.script.expected_leader}) | "
            f"{r.expected_leader_turn or '—'} | "
            f"{r.rejected_asks if r.script.rejected else '—'} | {rejected_p} |"
        )
    lines += ["", "## Ходы", ""]
    for r in results:
        lines += [f"### {r.script.id} · прогон {r.run}", ""]
        for t in r.turns:
            lines += [
                f"{t.n}. 👤 {t.client}",
                f"   🤖 {t.reply}",
                f"   ⚙ {t.dialog} · лидер {t.leader or '—'} · {t.top} · след. {t.next_slot}",
            ]
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
