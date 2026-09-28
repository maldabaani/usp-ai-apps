"""Watched repositories and issue runs (Postgres), plus an in-memory stand-in for tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import IssueRun, WatchedRepo


class WatchStore(Protocol):
    async def list_repos(self) -> Sequence[WatchedRepo]: ...

    async def get_repo(self, repo_id: int) -> WatchedRepo | None: ...

    async def add_repo(
        self, repo: str, *, poll_interval_s: int, extra_reviewers: list[str]
    ) -> WatchedRepo: ...

    async def update_repo(self, repo_id: int, **values: Any) -> WatchedRepo | None: ...

    async def delete_repo(self, repo_id: int) -> bool: ...

    async def issue_runs(self, repo: str | None = None) -> Sequence[IssueRun]: ...

    async def issue_run_for(self, run_id: str) -> IssueRun | None: ...

    async def add_issue_run(
        self, *, repo: str, issue_number: int, trigger: str, run_id: str
    ) -> IssueRun: ...

    async def update_issue_run(self, issue_run_id: int, **values: Any) -> None: ...


class WatchRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list_repos(self) -> Sequence[WatchedRepo]:
        async with self._sessionmaker() as session:
            return (await session.scalars(select(WatchedRepo).order_by(WatchedRepo.repo))).all()

    async def get_repo(self, repo_id: int) -> WatchedRepo | None:
        async with self._sessionmaker() as session:
            return await session.get(WatchedRepo, repo_id)

    async def add_repo(
        self, repo: str, *, poll_interval_s: int, extra_reviewers: list[str]
    ) -> WatchedRepo:
        row = WatchedRepo(
            repo=repo,
            enabled=True,
            poll_interval_s=poll_interval_s,
            extra_reviewers=extra_reviewers,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return row

    async def update_repo(self, repo_id: int, **values: Any) -> WatchedRepo | None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                update(WatchedRepo).where(WatchedRepo.id == repo_id).values(**values)
            )
        return await self.get_repo(repo_id)

    async def delete_repo(self, repo_id: int) -> bool:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(delete(WatchedRepo).where(WatchedRepo.id == repo_id))
        return bool(getattr(result, "rowcount", 0))

    async def issue_runs(self, repo: str | None = None) -> Sequence[IssueRun]:
        query = select(IssueRun).order_by(IssueRun.created_at.desc())
        if repo is not None:
            query = query.where(IssueRun.repo == repo)
        async with self._sessionmaker() as session:
            return (await session.scalars(query)).all()

    async def issue_run_for(self, run_id: str) -> IssueRun | None:
        async with self._sessionmaker() as session:
            return await session.scalar(select(IssueRun).where(IssueRun.run_id == run_id))

    async def add_issue_run(
        self, *, repo: str, issue_number: int, trigger: str, run_id: str
    ) -> IssueRun:
        row = IssueRun(repo=repo, issue_number=issue_number, trigger=trigger, run_id=run_id)
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return row

    async def update_issue_run(self, issue_run_id: int, **values: Any) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                update(IssueRun).where(IssueRun.id == issue_run_id).values(**values)
            )


class InMemoryWatchStore:
    """Same contract as WatchRepository, without a database."""

    def __init__(self) -> None:
        self._repos: dict[int, WatchedRepo] = {}
        self._issue_runs: dict[int, IssueRun] = {}
        self._next = 1

    def _id(self) -> int:
        self._next += 1
        return self._next - 1

    async def list_repos(self) -> Sequence[WatchedRepo]:
        return sorted(self._repos.values(), key=lambda r: r.repo)

    async def get_repo(self, repo_id: int) -> WatchedRepo | None:
        return self._repos.get(repo_id)

    async def add_repo(
        self, repo: str, *, poll_interval_s: int, extra_reviewers: list[str]
    ) -> WatchedRepo:
        if any(r.repo == repo for r in self._repos.values()):
            raise ValueError(f"{repo} is already watched")
        row = WatchedRepo(
            id=self._id(),
            repo=repo,
            enabled=True,
            poll_interval_s=poll_interval_s,
            extra_reviewers=list(extra_reviewers),
            last_polled_at=None,
            last_error=None,
            created_at=datetime.now(UTC),
        )
        self._repos[row.id] = row
        return row

    async def update_repo(self, repo_id: int, **values: Any) -> WatchedRepo | None:
        row = self._repos.get(repo_id)
        if row is not None:
            for key, value in values.items():
                setattr(row, key, value)
        return row

    async def delete_repo(self, repo_id: int) -> bool:
        return self._repos.pop(repo_id, None) is not None

    async def issue_runs(self, repo: str | None = None) -> Sequence[IssueRun]:
        rows = [r for r in self._issue_runs.values() if repo is None or r.repo == repo]
        return sorted(rows, key=lambda r: r.id, reverse=True)

    async def issue_run_for(self, run_id: str) -> IssueRun | None:
        return next((r for r in self._issue_runs.values() if r.run_id == run_id), None)

    async def add_issue_run(
        self, *, repo: str, issue_number: int, trigger: str, run_id: str
    ) -> IssueRun:
        if any(
            (r.repo, r.issue_number, r.trigger) == (repo, issue_number, trigger)
            for r in self._issue_runs.values()
        ):
            raise ValueError("this label event already started a run")
        row = IssueRun(
            id=self._id(),
            repo=repo,
            issue_number=issue_number,
            trigger=trigger,
            run_id=run_id,
            comment_id=None,
            comment_text=None,
            created_at=datetime.now(UTC),
        )
        self._issue_runs[row.id] = row
        return row

    async def update_issue_run(self, issue_run_id: int, **values: Any) -> None:
        row = self._issue_runs.get(issue_run_id)
        if row is not None:
            for key, value in values.items():
                setattr(row, key, value)
