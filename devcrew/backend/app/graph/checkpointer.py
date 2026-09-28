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
    database_url: str, *, max_size: int = 10
) -> AsyncIterator[AsyncPostgresSaver]:
    """Yield a ready-to-use saver; creates/migrates its tables on entry."""
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        to_psycopg_conninfo(database_url),
        max_size=max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=15)
    try:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        yield saver
    finally:
        await pool.close()
