from eval.retrieval.metrics import CaseResult, summarize


def result(ranked: tuple[str, ...], expected: set[str], scope: str = "orem") -> CaseResult:
    return CaseResult("c", ranked, frozenset(expected), frozenset({"orem"}), scope, 10.0)


def test_summary_metrics() -> None:
    summary = summarize(
        [
            result(("a", "b"), {"a"}),
            result(("x", "y", "z", "b"), {"b"}, scope="metering"),
            result(("x",), {"missing"}),
        ]
    )
    assert summary.n == 3
    assert summary.hit_at_1 == 1 / 3
    assert summary.recall_at_4 == 2 / 3
    assert summary.mrr_at_10 == (1 + 1 / 4) / 3
    assert summary.scope_at_1 == 2 / 3
