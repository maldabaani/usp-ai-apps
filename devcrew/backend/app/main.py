"""FastAPI entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.config import Settings, get_settings
from app.container import Container
from app.health import format_failures, run_health_checks
from app.logging import configure_logging

logger = logging.getLogger(__name__)


class StartupHealthError(RuntimeError):
    pass


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container.build(settings)
        app.state.container = container
        try:
            report = await run_health_checks(
                settings, container.engine, container.llm.models.required_models()
            )
            for check in report.checks:
                log = logger.info if check.ok else logger.warning
                log("health %-13s %s  %s", check.name, "OK " if check.ok else "FAIL", check.detail)
            if report.critical_failures:
                if settings.startup_health_strict:
                    raise StartupHealthError(format_failures(report))
                logger.warning(format_failures(report))
            yield
        finally:
            await container.close()

    app = FastAPI(title="DevCrew", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
