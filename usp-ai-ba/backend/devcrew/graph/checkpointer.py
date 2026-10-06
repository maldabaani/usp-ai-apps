"""LangGraph Postgres checkpointer (runs survive backend restarts)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool


def to_psycopg_conninfo(database_url: str) -> str:
    """postgresql+asyncpg://... (SQLAlchemy) -> postgresql://... (psycopg)."""
    scheme, sep, rest = database_url.partition("://")
    if not sep:
        raise ValueError(f"invalid database URL: {database_url!r}")
    return f"{scheme.split('+', 1)[0]}://{rest}"


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str, *, max_size: int = 10, open_timeout_s: float = 5.0
) -> AsyncIterator[AsyncPostgresSaver]:
    """Yield a ready-to-use saver; creates/migrates its tables on entry.

    This runs inside api/main.py's single shared lifespan, so a slow/unreachable Postgres
    delays StoryForge's own startup too, not just DevCrew's routes (the failure is caught
    there and doesn't crash the app, but the wait itself still happens first). Keep
    open_timeout_s short -- it should only need to cover a healthy Postgres's own connection
    setup time, not mask a real outage."""
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        to_psycopg_conninfo(database_url),
        max_size=max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=open_timeout_s)
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
    finally:
        await pool.close()
