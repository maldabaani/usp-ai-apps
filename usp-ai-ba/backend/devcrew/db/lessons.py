"""Lessons proposed from run friction (Postgres), plus an in-memory stand-in for tests."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devcrew.db.models import Lesson, LessonStatus


class LessonStore(Protocol):
    async def list_lessons(self, *, status: str | None = None) -> Sequence[Lesson]: ...

    async def get_lesson(self, lesson_id: int) -> Lesson | None: ...

    async def add_lesson(
        self,
        *,
        run_id: str | None,
        task_id: str | None,
        stack: str,
        source: str,
        rule_text: str,
        evidence: str,
    ) -> Lesson: ...

    async def decide_lesson(
        self, lesson_id: int, *, status: str, rule_id: str | None
    ) -> Lesson | None: ...


class LessonRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list_lessons(self, *, status: str | None = None) -> Sequence[Lesson]:
        query = select(Lesson).order_by(Lesson.created_at.desc())
        if status is not None:
            query = query.where(Lesson.status == status)
        async with self._sessionmaker() as session:
            return (await session.scalars(query)).all()

    async def get_lesson(self, lesson_id: int) -> Lesson | None:
        async with self._sessionmaker() as session:
            return await session.get(Lesson, lesson_id)

    async def add_lesson(
        self,
        *,
        run_id: str | None,
        task_id: str | None,
        stack: str,
        source: str,
        rule_text: str,
        evidence: str,
    ) -> Lesson:
        row = Lesson(
            run_id=run_id,
            task_id=task_id,
            stack=stack,
            source=source,
            rule_text=rule_text,
            evidence=evidence,
            status=LessonStatus.PENDING,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return row

    async def decide_lesson(
        self, lesson_id: int, *, status: str, rule_id: str | None
    ) -> Lesson | None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                update(Lesson)
                .where(Lesson.id == lesson_id)
                .values(status=status, rule_id=rule_id, decided_at=datetime.now(UTC))
            )
        return await self.get_lesson(lesson_id)


class InMemoryLessonStore:
    """Same contract as LessonRepository, without a database."""

    def __init__(self) -> None:
        self._lessons: dict[int, Lesson] = {}
        self._next = 1

    def _id(self) -> int:
        self._next += 1
        return self._next - 1

    async def list_lessons(self, *, status: str | None = None) -> Sequence[Lesson]:
        rows = [r for r in self._lessons.values() if status is None or r.status == status]
        return sorted(rows, key=lambda r: r.id, reverse=True)

    async def get_lesson(self, lesson_id: int) -> Lesson | None:
        return self._lessons.get(lesson_id)

    async def add_lesson(
        self,
        *,
        run_id: str | None,
        task_id: str | None,
        stack: str,
        source: str,
        rule_text: str,
        evidence: str,
    ) -> Lesson:
        row = Lesson(
            id=self._id(),
            run_id=run_id,
            task_id=task_id,
            stack=stack,
            source=source,
            rule_text=rule_text,
            evidence=evidence,
            status=LessonStatus.PENDING,
            rule_id=None,
            created_at=datetime.now(UTC),
            decided_at=None,
        )
        self._lessons[row.id] = row
        return row

    async def decide_lesson(
        self, lesson_id: int, *, status: str, rule_id: str | None
    ) -> Lesson | None:
        row = self._lessons.get(lesson_id)
        if row is not None:
            row.status = status
            row.rule_id = rule_id
            row.decided_at = datetime.now(UTC)
        return row
