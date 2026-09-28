"""Tests-only tasks are merged into the task they test (Mac run: T3/T4 wrote only tests)."""

from __future__ import annotations

from typing import Any

from app.graph.nodes.planner import fold_test_tasks
from app.graph.state import Plan


def task(tid: str, files: list[str], deps: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": tid,
        "title": f"Task {tid}",
        "description": f"Do {tid}",
        "target_files": files,
        "depends_on": deps or [],
        "stack": "python",
        "story_ids": ["US1"],
        **extra,
    }


def plan(*tasks: dict[str, Any]) -> Plan:
    return Plan.model_validate(
        {
            "summary": "Bookmarks API",
            "user_stories": [{"id": "US1", "story": "s", "acceptance_criteria": ["a"]}],
            "tasks": list(tasks),
        }
    )


def test_tests_only_tasks_move_into_the_task_they_test() -> None:
    folded = fold_test_tasks(
        plan(
            task("T1", ["app/models.py"]),
            task("T2", ["app/router.py"], ["T1"]),
            task("T3", ["tests/test_models.py"], ["T1"]),
            task("T4", ["tests/test_router.py"], ["T1", "T2"]),
            task("T5", ["app/docs.py"], ["T4"]),
        )
    )
    by_id = {t.id: t for t in folded.tasks}
    assert sorted(by_id) == ["T1", "T2", "T5"]
    assert by_id["T1"].target_files == ["app/models.py", "tests/test_models.py"]
    # the most downstream dependency hosts the tests
    assert by_id["T2"].target_files == ["app/router.py", "tests/test_router.py"]
    assert "Tests (QA): Task T4" in by_id["T2"].description
    assert by_id["T5"].depends_on == ["T2"]
    assert "T3 into T1, T4 into T2" in folded.summary


def test_plans_without_tests_only_tasks_are_unchanged() -> None:
    original = plan(
        task("T1", ["app/models.py", "tests/test_models.py"]),
        task("T2", ["tests/test_health.py"]),  # no dependency: nothing to merge into
    )
    assert fold_test_tasks(original) is original
