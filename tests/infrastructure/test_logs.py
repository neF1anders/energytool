import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from energytool.application.trace import LlmCall, TurnTrace
from energytool.infrastructure.logs import (
    configure_logging,
    log_context,
    log_duration,
    mask_sensitive,
)
from energytool.infrastructure.telemetry import LogTelemetry


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging("DEBUG", "json", buffer)
    yield buffer
    logging.getLogger().handlers.clear()


def records(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("звоните +7 (912) 345-67-89", "звоните +7***-**-89"),
        ("тел 89123456789", "тел +7***-**-89"),
        ("почта ivan.petrov@zavod.ru", "почта i***@zavod.ru"),
        ("key sk-or-v1-abcdef123456", "key ***"),
        ("Authorization: Bearer abc.def", "Authorization: ***"),
        ("мощность 1 100 кВт, 1000000 кВт·ч", "мощность 1 100 кВт, 1000000 кВт·ч"),
    ],
)
def test_mask_sensitive(raw: str, expected: str) -> None:
    assert mask_sensitive(raw) == expected


def test_json_record_has_context_masked_data_and_args(stream: io.StringIO) -> None:
    log = logging.getLogger("energytool.test")
    with log_context(session_id="s1", turn=3):
        log.info("contact %s", "+79123456789", extra={"data": {"email": "a.b@c.ru"}})
    (record,) = records(stream)
    assert record["msg"] == "contact +7***-**-89"
    assert record["session_id"] == "s1"
    assert record["turn"] == 3
    assert record["data"] == {"email": "a***@c.ru"}


def test_context_is_restored_after_block(stream: io.StringIO) -> None:
    log = logging.getLogger("energytool.test")
    with log_context(qid="7"):
        pass
    log.info("outside")
    assert "qid" not in records(stream)[0]


def test_log_duration_reports_ms_and_failures(stream: io.StringIO) -> None:
    log = logging.getLogger("energytool.test")
    with log_duration(log, "step", n=1) as extra:
        extra["found"] = 2
    with pytest.raises(RuntimeError), log_duration(log, "boom"):
        raise RuntimeError("tel 89123456789")
    ok, failed = records(stream)
    assert ok["msg"] == "step"
    assert ok["data"]["found"] == 2
    assert isinstance(ok["data"]["ms"], float)
    assert failed["level"] == "ERROR"
    assert "+7***-**-89" in failed["exc"]
    assert "89123456789" not in failed["exc"]


def test_text_format_renders_context_and_data() -> None:
    buffer = io.StringIO()
    configure_logging("INFO", "text", buffer)
    with log_context(qid="5"):
        logging.getLogger("energytool.test").info("hi", extra={"data": {"k": 1}})
    logging.getLogger().handlers.clear()
    line = buffer.getvalue()
    assert "hi [qid=5]" in line
    assert '{"k": 1}' in line


def test_telemetry_writes_trace_event(stream: io.StringIO) -> None:
    trace = TurnTrace(session_id="s", turn=1, mode="batch")
    trace.llm_calls.append(LlmCall(kind="chat", model="m", ms=12.0))
    LogTelemetry().record(trace)
    (record,) = records(stream)
    assert record["msg"] == "turn_trace"
    assert record["data"]["llm_calls"][0]["model"] == "m"
