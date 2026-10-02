"""Lessons: candidate rule bullets proposed from run friction (see devcrew/learning.py),
queued for human approval before devcrew/tools/catalog.py's RulesCatalog.append_rule() adds
them to the stack's own rules file. Listing is any authenticated user; approve/reject are
admin-only -- approving permanently changes the rules every future run reads, same gating as
github.py's watched-repo mutations.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.deps import require_admin, require_auth
from devcrew.api.deps import ContainerDep
from devcrew.db.models import Lesson, LessonStatus
from devcrew.tools.base import ToolError

router = APIRouter(prefix="/lessons", tags=["lessons"], dependencies=[Depends(require_auth)])


class LessonOut(BaseModel):
    id: int
    run_id: str | None
    task_id: str | None
    stack: str
    source: str
    rule_text: str
    evidence: str
    status: str
    rule_id: str | None
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def of(cls, row: Lesson) -> LessonOut:
        return cls(
            id=row.id,
            run_id=row.run_id,
            task_id=row.task_id,
            stack=row.stack,
            source=row.source,
            rule_text=row.rule_text,
            evidence=row.evidence,
            status=row.status,
            rule_id=row.rule_id,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )


async def _get_pending(container: ContainerDep, lesson_id: int) -> Lesson:
    row = await container.lessons.get_lesson(lesson_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"lesson {lesson_id} not found")
    if row.status != LessonStatus.PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"lesson {lesson_id} was already {row.status}"
        )
    return row


@router.get("", response_model=list[LessonOut])
async def list_lessons(
    container: ContainerDep, status_: str | None = Query(default=None, alias="status")
) -> list[LessonOut]:
    return [LessonOut.of(r) for r in await container.lessons.list_lessons(status=status_)]


@router.post(
    "/{lesson_id}/approve", response_model=LessonOut, dependencies=[Depends(require_admin)]
)
async def approve_lesson(lesson_id: int, container: ContainerDep) -> LessonOut:
    lesson = await _get_pending(container, lesson_id)
    try:
        rule_id = container.deps.rules.append_rule(lesson.stack, lesson.rule_text)
    except ToolError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    row = await container.lessons.decide_lesson(
        lesson_id, status=LessonStatus.APPROVED, rule_id=rule_id
    )
    assert row is not None
    return LessonOut.of(row)


@router.post(
    "/{lesson_id}/reject", response_model=LessonOut, dependencies=[Depends(require_admin)]
)
async def reject_lesson(lesson_id: int, container: ContainerDep) -> LessonOut:
    await _get_pending(container, lesson_id)
    row = await container.lessons.decide_lesson(
        lesson_id, status=LessonStatus.REJECTED, rule_id=None
    )
    assert row is not None
    return LessonOut.of(row)
