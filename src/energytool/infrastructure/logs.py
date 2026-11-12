"""Логи: настройка stdlib logging, JSON-строки, маскирование ПДн и секретов, контекст хода.

Договорённости (docs/spec.md §8.5):
- domain не логирует — его решения попадают в TurnTrace;
- application и infrastructure пишут в logging.getLogger(__name__);
- структурированные поля передаются через extra={"data": {...}};
- контекст (session_id, turn, qid) выставляет interface через log_context();
- телефоны, e-mail, ключи и Bearer-токены маскируются фильтром на хендлере,
  поэтому защищены все логгеры, включая сторонние (httpx).

Логи идут в stderr: на платформе stdout остаётся для прогресса прогона.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import MappingProxyType
from typing import IO, Literal

LogFormat = Literal["json", "text"]

_context: ContextVar[Mapping[str, object]] = ContextVar("log_context", default=MappingProxyType({}))

_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

_PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*(\d{2})(?!\d)")
_EMAIL = re.compile(r"\b([\w.+-])[\w.+-]*@([\w-]+(?:\.[\w-]+)+)\b")
_SECRET = re.compile(r"\b(?:sk-[\w-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


def mask_sensitive(text: str) -> str:
    """Телефон → +7***-**-NN, e-mail → a***@domain, ключи → ***."""
    text = _SECRET.sub("***", text)
    text = _PHONE.sub(lambda m: f"+7***-**-{m.group(1)}", text)
    return _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)


def _mask_value(value: object) -> object:
    if isinstance(value, str):
        return mask_sensitive(value)
    if isinstance(value, Mapping):
        return {key: _mask_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_value(item) for item in value]
    return value


class SensitiveDataFilter(logging.Filter):
    """Маскирует сообщение и структурированные поля до форматирования."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_sensitive(record.getMessage())
        record.args = None
        data = getattr(record, "data", None)
        if data is not None:
            record.data = _mask_value(data)
        if record.exc_info and not record.exc_text:
            record.exc_text = mask_sensitive(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class ContextFilter(logging.Filter):
    """Добавляет к записи текущий контекст хода."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.ctx = dict(_context.get())
        return True


class JsonFormatter(logging.Formatter):
    """Одна запись — одна JSON-строка: удобно грепать и грузить в анализ прогона."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "ctx", {}))
        data = getattr(record, "data", None)
        if data is not None:
            payload["data"] = data
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key not in {"ctx", "data"}:
                payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Человекочитаемый вид для локальной отладки."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-5s %(name)s %(message)s", "%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        record.asctime = self.formatTime(record, self.datefmt)
        line = self.formatMessage(record)
        ctx = getattr(record, "ctx", {})
        if ctx:
            line += " [" + " ".join(f"{k}={v}" for k, v in ctx.items()) + "]"
        data = getattr(record, "data", None)
        if data is not None:
            line += " " + json.dumps(data, ensure_ascii=False, default=str)
        if record.exc_text:
            line += "\n" + record.exc_text
        return line


def configure_logging(
    level: str = "INFO", fmt: LogFormat = "json", stream: IO[str] | None = None
) -> None:
    """Настройка корневого логгера. Идемпотентна: повторный вызов заменяет хендлер."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(ContextFilter())
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # httpx на INFO пишет каждый запрос; свои вызовы gateway логирует сам
    logging.getLogger("httpx").setLevel(max(logging.WARNING, root.level))
    logging.getLogger("httpcore").setLevel(max(logging.WARNING, root.level))
    # bm25s сам выставляет своему логгеру DEBUG
    logging.getLogger("bm25s").setLevel(max(logging.WARNING, root.level))


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Поля контекста (session_id, turn, qid) попадают во все записи внутри блока,
    в том числе из потоков пула, если блок открыт внутри задачи потока."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


@contextmanager
def log_duration(
    logger: logging.Logger, event: str, level: int = logging.DEBUG, **data: object
) -> Iterator[dict[str, object]]:
    """Замер блока: пишет event с ms и полями; в yield-словарь можно дописать результат."""
    extra: dict[str, object] = dict(data)
    started = time.perf_counter()
    try:
        yield extra
    except Exception:
        extra["ms"] = round((time.perf_counter() - started) * 1000, 1)
        logger.exception("%s failed", event, extra={"data": extra})
        raise
    extra["ms"] = round((time.perf_counter() - started) * 1000, 1)
    logger.log(level, event, extra={"data": extra})
