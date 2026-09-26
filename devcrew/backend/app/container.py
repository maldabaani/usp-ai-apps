"""Process-wide singletons, built once in the FastAPI lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.repository import RunRepository, RunStore
from app.db.session import create_engine, create_sessionmaker
from app.db.watch import InMemoryWatchStore, WatchRepository, WatchStore
from app.events.bus import EventBus
from app.events.store import PostgresEventStore
from app.graph.backbone import build_graph
from app.graph.checkpointer import postgres_checkpointer
from app.graph.factory import build_deps, build_llm
from app.graph.runner import RunDriver
from app.graph.runtime import GraphDeps
from app.llm.client import LLMGateway
from app.services.github_watch import GitHubWatcher
from app.services.run_manager import RunManager


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine | None
    runs: RunStore
    events: EventBus
    llm: LLMGateway
    deps: GraphDeps
    driver: RunDriver
    manager: RunManager
    watch: WatchStore
    watcher: GitHubWatcher

    @classmethod
    def assemble(
        cls,
        settings: Settings,
        *,
        deps: GraphDeps,
        runs: RunStore,
        checkpointer: BaseCheckpointSaver[Any],
        engine: AsyncEngine | None = None,
        watch: WatchStore | None = None,
    ) -> Container:
        driver = RunDriver(build_graph(deps, checkpointer), deps.events, runs)
        manager = RunManager(driver, runs, deps.events, deps)
        watch = watch if watch is not None else InMemoryWatchStore()
        return cls(
            settings=settings,
            engine=engine,
            runs=runs,
            events=deps.events,
            llm=deps.llm,
            deps=deps,
            driver=driver,
            manager=manager,
            watch=watch,
            watcher=GitHubWatcher(settings, manager, watch, deps.github),
        )

    @classmethod
    @asynccontextmanager
    async def open(cls, settings: Settings, engine: AsyncEngine) -> AsyncIterator[Container]:
        """Production wiring: Postgres app tables, event log and LangGraph checkpointer."""
        sessionmaker = create_sessionmaker(engine)
        events = EventBus(PostgresEventStore(sessionmaker))
        deps = build_deps(settings, events, llm=build_llm(settings))
        async with postgres_checkpointer(settings.database_url) as saver:
            container = cls.assemble(
                settings,
                deps=deps,
                runs=RunRepository(sessionmaker),
                checkpointer=saver,
                engine=engine,
                watch=WatchRepository(sessionmaker),
            )
            try:
                yield container
            finally:
                await container.manager.shutdown()


def default_engine(settings: Settings) -> AsyncEngine:
    return create_engine(settings.database_url)
