"""Composition root: Settings → адаптеры → use cases. Единственное место сборки.

Точки входа (CLI, batch, HTTP) получают готовый Container и не создают адаптеры сами.
По мере этапов сюда добавляются индекс базы, ретрив, Extractor/Responder и use cases.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from energytool.infrastructure.config import Settings
from energytool.infrastructure.llm.gateway import LlmGateway
from energytool.infrastructure.logs import configure_logging
from energytool.infrastructure.telemetry import LogTelemetry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    gateway: LlmGateway
    telemetry: LogTelemetry

    def close(self) -> None:
        self.gateway.close()


def build(env: Mapping[str, str] | None = None) -> Container:
    settings = Settings.from_env(os.environ if env is None else env)
    configure_logging(settings.log_level, settings.log_format)
    logger.info("settings loaded", extra={"data": settings.safe_summary()})
    return Container(
        settings=settings,
        gateway=LlmGateway(settings),
        telemetry=LogTelemetry(),
    )
