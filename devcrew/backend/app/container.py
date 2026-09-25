"""Process-wide singletons, built once in the FastAPI lifespan."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.repository import RunRepository
from app.db.session import create_engine, create_sessionmaker
from app.events.bus import EventBus
from app.events.store import PostgresEventStore
from app.llm.client import LLMGateway
from app.llm.models_config import load_models_config


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    runs: RunRepository
    events: EventBus
    llm: LLMGateway

    @classmethod
    def build(cls, settings: Settings) -> Container:
        engine = create_engine(settings.database_url)
        sessionmaker = create_sessionmaker(engine)
        models = load_models_config(settings.models_config_path)
        return cls(
            settings=settings,
            engine=engine,
            sessionmaker=sessionmaker,
            runs=RunRepository(sessionmaker),
            events=EventBus(PostgresEventStore(sessionmaker)),
            llm=LLMGateway(
                models,
                base_url=settings.ollama_base_url,
                max_parallel=settings.max_parallel_devs,
                request_timeout_s=settings.llm_request_timeout_s,
            ),
        )

    async def close(self) -> None:
        await self.engine.dispose()
