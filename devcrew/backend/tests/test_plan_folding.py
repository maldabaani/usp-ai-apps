"""Tests-only tasks are merged into the task they test (Mac run: T3/T4 wrote only tests)."""

from __future__ import annotations

from typing import Any

from app.graph.nodes.planner import fold_test_tasks, merge_serial_plan
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


def test_a_small_serial_plan_becomes_one_task() -> None:
    # the Mac run: one endpoint planned as model -> service -> controller -> endpoint
    merged = merge_serial_plan(
        plan(
            task("T1", ["app/schemas/bookmark.py"]),
            task("T2", ["app/services/bookmarks.py"], ["T1"]),
            task("T3", ["app/controllers/bookmarks.py"], ["T2"]),
            task("T4", ["app/routers/bookmarks.py", "tests/test_bookmarks.py"], ["T3"]),
        )
    )
    [only] = merged.tasks
    assert only.id == "T1" and only.depends_on == []
    assert only.title == "Task T4 (complete feature)"
    assert only.target_files == [
        "app/schemas/bookmark.py",
        "app/services/bookmarks.py",
        "app/controllers/bookmarks.py",
        "app/routers/bookmarks.py",
        "tests/test_bookmarks.py",
    ]
    assert "1. Task T1: Do T1" in only.description and "4. Task T4: Do T4" in only.description
    assert "merged into one task (T1, T2, T3, T4)" in merged.summary
    assert merged.shape() == "1 task"


def test_parallel_or_short_plans_are_kept() -> None:
    parallel = plan(
        task("T1", ["app/schemas/bookmark.py"]),
        task("T2", ["app/routers/create.py"], ["T1"]),
        task("T3", ["app/routers/list.py"], ["T1"]),
        task("T4", ["app/routers/delete.py"], ["T1"]),
    )
    assert merge_serial_plan(parallel) is parallel
    assert parallel.shape() == "4 tasks in 2 steps, up to 3 in parallel"
    short = plan(task("T1", ["app/a.py"]), task("T2", ["app/b.py"], ["T1"]))
    assert merge_serial_plan(short) is short
    assert short.shape() == "2 tasks, one after another"
