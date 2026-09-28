"""Event persistence backends."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import RunEvent
from app.events.types import Event, EventIn, EventType


class EventStore(Protocol):
    async def append(self, event: EventIn) -> Event: ...

    async def list_after(self, run_id: str, after_id: int, limit: int = 500) -> list[Event]: ...


class InMemoryEventStore:
    """Used in tests and headless benchmark runs."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    async def append(self, event: EventIn) -> Event:
        stored = Event(id=len(self._events) + 1, **event.model_dump())
        self._events.append(stored)
        return stored

    async def list_after(self, run_id: str, after_id: int, limit: int = 500) -> list[Event]:
        matching = [e for e in self._events if e.run_id == run_id and e.id > after_id]
        return matching[:limit]


class PostgresEventStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def append(self, event: EventIn) -> Event:
        stmt = (
            insert(RunEvent)
            .values(
                run_id=event.run_id,
                type=event.type.value,
                node=event.node,
                task_id=event.task_id,
                payload=event.payload,
            )
            .returning(RunEvent.id, RunEvent.created_at)
        )
        async with self._sessionmaker() as session, session.begin():
            row = (await session.execute(stmt)).one()
        return Event(id=row.id, created_at=row.created_at, **event.model_dump())

    async def list_after(self, run_id: str, after_id: int, limit: int = 500) -> list[Event]:
        stmt = (
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.id > after_id)
            .order_by(RunEvent.id)
            .limit(limit)
        )
        async with self._sessionmaker() as session:
            rows = (await session.scalars(stmt)).all()
        return [
            Event(
                id=r.id,
                run_id=r.run_id,
                type=EventType(r.type),
                node=r.node,
                task_id=r.task_id,
                payload=r.payload,
                created_at=r.created_at,
            )
            for r in rows
        ]
