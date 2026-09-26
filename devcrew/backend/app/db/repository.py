"""Data access for the runs table (Postgres) and an in-memory stand-in for tests/headless use."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Run, RunStatus


def new_run_id() -> str:
    return uuid.uuid4().hex


class RunStore(Protocol):
    async def create(self, *, request: str, repo_target: str, create_repo: bool) -> Run: ...

    async def get(self, run_id: str) -> Run | None: ...

    async def list(self, limit: int = 100) -> Sequence[Run]: ...

    async def update(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        pr_url: str | None = None,
        error: str | None = None,
        clear_error: bool = False,
    ) -> None: ...


class RunRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(self, *, request: str, repo_target: str, create_repo: bool) -> Run:
        run = Run(
            id=new_run_id(),
            request=request,
            repo_target=repo_target,
            create_repo=create_repo,
            status=RunStatus.PENDING,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(run)
            await session.flush()
            await session.refresh(run)  # load server-side defaults (timestamps)
        return run

    async def get(self, run_id: str) -> Run | None:
        async with self._sessionmaker() as session:
            return await session.get(Run, run_id)

    async def list(self, limit: int = 100) -> Sequence[Run]:
        async with self._sessionmaker() as session:
            result = await session.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit))
            return result.all()

    async def update(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        pr_url: str | None = None,
        error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        values: dict[str, object] = {}
        if status is not None:
            values["status"] = status.value
        if pr_url is not None:
            values["pr_url"] = pr_url
        if error is not None:
            values["error"] = error
        if clear_error:
            values["error"] = None
        if not values:
            return
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(Run).where(Run.id == run_id).values(**values))


class InMemoryRunStore:
    """Same contract as RunRepository, without a database (tests, headless benchmark)."""

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    async def create(self, *, request: str, repo_target: str, create_repo: bool) -> Run:
        now = datetime.now(UTC)
        run = Run(
            id=new_run_id(),
            request=request,
            repo_target=repo_target,
            create_repo=create_repo,
            status=RunStatus.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        self._runs[run.id] = run
        return run

    async def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def list(self, limit: int = 100) -> Sequence[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    async def update(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        pr_url: str | None = None,
        error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        if status is not None:
            run.status = status.value
        if pr_url is not None:
            run.pr_url = pr_url
        if error is not None:
            run.error = error
        if clear_error:
            run.error = None
        run.updated_at = datetime.now(UTC)
