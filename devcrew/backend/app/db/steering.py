"""Chat messages to a run and the pause flag (Phase 13), plus an in-memory stand-in."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Run, RunMessage

OPEN = "pending"


class SteeringStore(Protocol):
    async def add_message(self, run_id: str, text: str, task_id: str | None) -> RunMessage: ...

    async def messages(self, run_id: str) -> Sequence[RunMessage]: ...

    async def update_messages(self, ids: Iterable[int], **values: Any) -> None: ...

    async def pause_requested(self, run_id: str) -> bool: ...

    async def set_pause(self, run_id: str, value: bool) -> None: ...


class SteeringRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def add_message(self, run_id: str, text: str, task_id: str | None) -> RunMessage:
        row = RunMessage(run_id=run_id, task_id=task_id, text=text, status=OPEN)
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return row

    async def messages(self, run_id: str) -> Sequence[RunMessage]:
        query = select(RunMessage).where(RunMessage.run_id == run_id).order_by(RunMessage.id)
        async with self._sessionmaker() as session:
            return (await session.scalars(query)).all()

    async def update_messages(self, ids: Iterable[int], **values: Any) -> None:
        ids = list(ids)
        if not ids:
            return
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(RunMessage).where(RunMessage.id.in_(ids)).values(**values))

    async def pause_requested(self, run_id: str) -> bool:
        async with self._sessionmaker() as session:
            value = await session.scalar(select(Run.pause_requested).where(Run.id == run_id))
        return bool(value)

    async def set_pause(self, run_id: str, value: bool) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(Run).where(Run.id == run_id).values(pause_requested=value))


class InMemorySteeringStore:
    """Same contract as SteeringRepository, without a database."""

    def __init__(self) -> None:
        self._messages: dict[int, RunMessage] = {}
        self._paused: set[str] = set()

    async def add_message(self, run_id: str, text: str, task_id: str | None) -> RunMessage:
        now = datetime.now(UTC)
        row = RunMessage(
            id=len(self._messages) + 1,
            run_id=run_id,
            task_id=task_id,
            text=text,
            status=OPEN,
            action=None,
            reply=None,
            created_at=now,
            updated_at=now,
        )
        self._messages[row.id] = row
        return row

    async def messages(self, run_id: str) -> Sequence[RunMessage]:
        return [m for m in self._messages.values() if m.run_id == run_id]

    async def update_messages(self, ids: Iterable[int], **values: Any) -> None:
        for i in ids:
            row = self._messages.get(i)
            if row is not None:
                for key, value in values.items():
                    setattr(row, key, value)
                row.updated_at = datetime.now(UTC)

    async def pause_requested(self, run_id: str) -> bool:
        return run_id in self._paused

    async def set_pause(self, run_id: str, value: bool) -> None:
        if value:
            self._paused.add(run_id)
        else:
            self._paused.discard(run_id)
