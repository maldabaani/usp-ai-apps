"""The report's task table after a cancelled run (Mac run: T2 read "in_progress, 1 attempt")."""

from __future__ import annotations

from typing import Any

from devcrew.github.pr_body import _attempts
from devcrew.services.report import _final_tasks


def test_unfinished_tasks_of_a_cancelled_run() -> None:
    tasks: dict[str, dict[str, Any]] = {
        "T1": {"status": "merged", "iterations": 2},
        "T2": {"status": "in_progress", "iterations": 1, "coordinator_actions": 1},
        "T3": {"status": "pending"},
    }
    final = _final_tasks(tasks, "cancelled")
    assert [t["status"] for t in final.values()] == [
        "merged",
        "stopped (run cancelled)",
        "not started (run cancelled)",
    ]
    assert _final_tasks(tasks, "executing") == tasks


def test_attempts_mention_coordinator_retries() -> None:
    assert _attempts({"iterations": 2}) == "2"
    assert _attempts({"iterations": 1, "coordinator_actions": 1}) == (
        "1 (after 1 Coordinator action)"
    )


def test_unfinished_summary() -> None:
    from devcrew.graph.state import unfinished_summary

    tasks = {
        "T1": {"status": "merged"},
        "T2": {"status": "failed"},
        "T3": {"status": "blocked"},
        "T4": {"status": "blocked"},
        "T5": {"status": "cancelled"},  # dropped on purpose: not a failure
    }
    assert unfinished_summary(tasks) == "T2 failed; T3, T4 blocked"
    assert unfinished_summary({"T1": {"status": "merged"}}) is None
