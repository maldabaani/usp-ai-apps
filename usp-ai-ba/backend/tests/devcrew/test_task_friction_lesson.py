"""make_merge()'s lesson-capture hook: a merged task's friction (Coordinator retries,
escalations, review cycles) is proposed as a candidate lesson, queued for human approval."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from langchain_core.messages import AIMessage

from devcrew.db.models import LessonSource, LessonStatus
from tests.devcrew.fakes import Call, final, tool_call
from tests.devcrew.graph_harness import APPROVE, PLAN, make_harness
from tests.devcrew.test_parallel import task, writing_developer

RUN = "run0009frictionlesson0000"


async def start(h: Any, plan: dict[str, Any]) -> Any:
    h.brain.responders["planner"] = lambda c: final(plan)
    await h.driver.start(RUN, "Build it", "me/x", False)
    await h.driver.resume(RUN, APPROVE)
    return await h.driver.resume(RUN, APPROVE)


async def test_merged_task_with_a_coordinator_retry_proposes_a_lesson(tmp_path: Path) -> None:
    broken = {"n": 0}

    def developer(call: Call) -> AIMessage:
        if "Coordinator guidance: call write_file" not in call.text() and broken["n"] < 2:
            broken["n"] += 1
            return tool_call("nonexistent", x=1)
        return cast(AIMessage, writing_developer()(call))

    h = make_harness(tmp_path)
    h.brain.responders["developer"] = developer
    h.brain.responders["coordinator"] = lambda c: final(
        {"action": "retry", "reason": "tool misuse", "guidance": "call write_file with path"}
    )
    h.brain.responders["lesson summarizer"] = lambda c: final(
        {
            "worth_recording": True,
            "rule_text": "Call tools with their real, documented names.",
        }
    )

    await start(h, {**PLAN, "tasks": [task("A")]})

    lessons = await h.deps.lessons.list_lessons()
    assert len(lessons) == 1
    assert lessons[0].task_id == "A"
    assert lessons[0].status == LessonStatus.PENDING
    assert lessons[0].source == LessonSource.COORDINATOR_ESCALATION
    assert "tool" in lessons[0].evidence.lower()


async def test_merged_task_with_no_friction_proposes_no_lesson(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await start(h, {**PLAN, "tasks": [task("A")]})
    assert await h.deps.lessons.list_lessons() == []


async def test_merged_task_with_only_a_review_cycle_uses_review_cycle_source(
    tmp_path: Path,
) -> None:
    rejected = {"n": 0}

    def reviewer(call: Call) -> AIMessage:
        if rejected["n"] == 0:
            rejected["n"] += 1
            return final(
                {
                    "decision": "changes_requested",
                    "summary": "needs work",
                    "issues": [
                        {"file": "app/a.py", "severity": "blocker", "message": "wrong approach"}
                    ],
                }
            )
        return final({"decision": "approve", "summary": "ok", "issues": []})

    h = make_harness(tmp_path)
    h.brain.responders["developer"] = writing_developer()
    h.brain.responders["reviewer"] = reviewer
    h.brain.responders["lesson summarizer"] = lambda c: final(
        {"worth_recording": True, "rule_text": "Handle the edge case the reviewer flagged."}
    )

    await start(h, {**PLAN, "tasks": [task("A")]})

    lessons = await h.deps.lessons.list_lessons()
    assert len(lessons) == 1
    assert lessons[0].source == LessonSource.REVIEW_CYCLE
