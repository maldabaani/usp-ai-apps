"""Data access for the runs table."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Run, RunStatus


def new_run_id() -> str:
    return uuid.uuid4().hex


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
    ) -> None:
        values: dict[str, object] = {}
        if status is not None:
            values["status"] = status.value
        if pr_url is not None:
            values["pr_url"] = pr_url
        if error is not None:
            values["error"] = error
        if not values:
            return
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(Run).where(Run.id == run_id).values(**values))
