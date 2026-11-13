"""Адаптер порта Telemetry: TurnTrace JSON-строкой в лог (событие turn_trace).

Запись в таблицу traces добавит SessionRepo (M5); этот адаптер нужен и батчу, и вебу.
"""

from __future__ import annotations

import logging

from energytool.application.trace import TurnTrace

logger = logging.getLogger("energytool.trace")


class LogTelemetry:
    def record(self, trace: TurnTrace) -> None:
        logger.info("turn_trace", extra={"data": trace.to_dict()})
