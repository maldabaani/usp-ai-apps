"""FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import github, health, runs, workspace
from app.config import Settings, get_settings
from app.container import Container, default_engine
from app.graph.factory import build_llm
from app.health import format_failures, run_health_checks
from app.logging import configure_logging

logger = logging.getLogger(__name__)

ContainerFactory = Callable[[Settings], AbstractAsyncContextManager[Container]]


class StartupHealthError(RuntimeError):
    pass


@asynccontextmanager
async def production_container(settings: Settings) -> AsyncIterator[Container]:
    engine = default_engine(settings)
    try:
        report = await run_health_checks(
            settings, engine, build_llm(settings).models.required_models()
        )
        for check in report.checks:
            log = logger.info if check.ok else logger.warning
            log("health %-14s %s  %s", check.name, "OK " if check.ok else "FAIL", check.detail)
        if report.critical_failures:
            if settings.startup_health_strict:
                raise StartupHealthError(format_failures(report))
            logger.warning(format_failures(report))
        async with Container.open(settings, engine) as container:
            yield container
    finally:
        await engine.dispose()


def create_app(
    settings: Settings | None = None, container_factory: ContainerFactory | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    factory = container_factory or production_container

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with factory(settings) as container:
            app.state.container = container
            recovered = await container.manager.recover()
            if recovered:
                logger.info("resumed %d run(s) after restart: %s", len(recovered), recovered)
            watcher = None
            if container.watcher.github is not None:  # GitHub automation needs a token
                watcher = asyncio.create_task(container.watcher.run_forever())
            try:
                yield
            finally:
                if watcher is not None:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher

    app = FastAPI(title="DevCrew", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(workspace.router)
    app.include_router(github.router)
    return app


app = create_app()
