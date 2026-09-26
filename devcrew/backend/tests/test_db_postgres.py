"""Integration tests against a real Postgres (set TEST_DATABASE_URL; skipped otherwise)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import RunStatus
from app.db.repository import RunRepository
from app.db.session import create_sessionmaker
from app.events.bus import EventBus
from app.events.store import PostgresEventStore
from app.events.types import EventType


async def test_run_repository_roundtrip(pg_engine: AsyncEngine) -> None:
    repo = RunRepository(create_sessionmaker(pg_engine))
    run = await repo.create(request="todo api", repo_target="me/todo", create_repo=True)
    await repo.update(run.id, status=RunStatus.PLANNING)
    loaded = await repo.get(run.id)
    assert loaded is not None
    assert (loaded.status, loaded.create_repo) == (RunStatus.PLANNING, True)
    assert [r.id for r in await repo.list()] == [run.id]
    assert await repo.get("missing") is None


async def test_postgres_event_replay(pg_engine: AsyncEngine) -> None:
    sm = create_sessionmaker(pg_engine)
    run = await RunRepository(sm).create(request="x", repo_target="me/x", create_repo=False)
    bus = EventBus(PostgresEventStore(sm))
    published = [
        await bus.publish(run.id, EventType.NODE_STARTED, node="planner", payload={"n": i})
        for i in range(3)
    ]
    assert published[0].id < published[1].id < published[2].id

    sub = bus.subscribe(run.id, last_event_id=published[0].id)
    got = []
    async with asyncio.timeout(2):
        async for event in sub:
            got.append(event)
            if len(got) == 2:
                break
    await sub.aclose()
    assert [e.payload["n"] for e in got] == [1, 2]
    assert got[0].type is EventType.NODE_STARTED and got[0].node == "planner"
