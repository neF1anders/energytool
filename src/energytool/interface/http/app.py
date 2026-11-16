"""FastAPI-приложение: индекс и калибровка thinking на старте, обработчики синхронные."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from energytool.bootstrap import build
from energytool.bootstrap_web import WebServices, build_web
from energytool.interface.http.routes import router

logger = logging.getLogger(__name__)


def default_services() -> WebServices:
    container = build()
    container.gateway.calibrate_thinking()
    return build_web(container)


def create_app(services_factory: Callable[[], WebServices] = default_services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = services_factory()
        app.state.services = services
        logger.info("http app started")
        yield
        services.container.close()

    app = FastAPI(title="Energytool Assistant", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app
