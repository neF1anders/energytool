"""TurnTrace → DebugInfo: документы с путями разделов для режима отладки."""

from __future__ import annotations

from dataclasses import asdict

from energytool.application.build_index import KnowledgeIndex
from energytool.application.trace import TurnTrace
from energytool.interface.http.schemas import DebugInfo


def debug_info(trace: TurnTrace, index: KnowledgeIndex) -> DebugInfo:
    chunks = {c.id: c for c in index.chunks.get([e.section_id for e in trace.evidence])}
    return DebugInfo(
        turn=trace.turn,
        total_ms=trace.timings_ms.get("total", 0.0),
        timings_ms=trace.timings_ms,
        documents=[
            {
                "id": e.section_id,
                "title": chunks[e.section_id].path if e.section_id in chunks else e.section_id,
                "file": chunks[e.section_id].file if e.section_id in chunks else "",
                "origin": e.origin,
                "score": e.score,
            }
            for e in trace.evidence
        ],
        rule_hits=[asdict(h) for h in trace.rule_hits],
        dialog=trace.dialog,
        scores=trace.scores_after,
        next_slot=trace.next_slot,
        llm_calls=[asdict(c) for c in trace.llm_calls],
        degraded=trace.degraded,
        extraction=trace.extraction,
    )
