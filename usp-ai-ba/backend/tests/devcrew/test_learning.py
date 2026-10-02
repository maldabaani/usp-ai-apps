"""propose_lesson(): turning real run friction into a candidate rule, queued for approval."""

from __future__ import annotations

from pathlib import Path

from devcrew.db.lessons import InMemoryLessonStore
from devcrew.db.models import LessonStatus
from devcrew.learning import propose_lesson
from tests.devcrew.fakes import Brain, final
from tests.devcrew.graph_harness import make_harness

RUN = "run-learning-test000000000"


async def test_propose_lesson_stores_a_pending_lesson_when_worth_recording(tmp_path: Path) -> None:
    brain = Brain(
        responders={
            "lesson summarizer": lambda c: final(
                {
                    "worth_recording": True,
                    "rule_text": "A service method catches driver-specific exceptions and "
                    "raises the application's own exception type instead.",
                }
            )
        }
    )
    h = make_harness(tmp_path, brain=brain)
    lessons = InMemoryLessonStore()

    lesson = await propose_lesson(
        lessons,
        h.deps.llm,
        h.deps.prompts,
        run_id=RUN,
        task_id="T1",
        stack="python",
        source="coordinator_escalation",
        evidence="The developer let a raw OperationalError propagate out of save().",
    )

    assert lesson is not None
    assert lesson.status == LessonStatus.PENDING
    assert lesson.rule_id is None
    assert "exception" in lesson.rule_text
    stored = await lessons.list_lessons()
    assert [s.id for s in stored] == [lesson.id]


async def test_propose_lesson_returns_none_when_not_worth_recording(tmp_path: Path) -> None:
    brain = Brain(
        responders={
            "lesson summarizer": lambda c: final({"worth_recording": False, "rule_text": ""})
        }
    )
    h = make_harness(tmp_path, brain=brain)
    lessons = InMemoryLessonStore()

    lesson = await propose_lesson(
        lessons,
        h.deps.llm,
        h.deps.prompts,
        run_id=RUN,
        task_id="T1",
        stack="python",
        source="review_cycle",
        evidence="A one-off typo the reviewer caught on the first pass.",
    )

    assert lesson is None
    assert await lessons.list_lessons() == []
