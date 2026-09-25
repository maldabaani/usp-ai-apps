from __future__ import annotations

from app.graph.context_builder import Section, developer_context, fit_sections
from app.graph.state import Design, Plan, TaskState
from app.llm.tokens import estimate_tokens
from tests.graph_harness import DESIGN, PLAN


def test_fits_budget_by_truncating_low_priority_first() -> None:
    sections = [
        Section("Task", "do it", priority=0, required=True),
        Section("Contracts", "c\n" * 400, priority=2),
        Section("Files", "f\n" * 4000, priority=6),
    ]
    out = fit_sections(sections, 600)
    assert estimate_tokens(out) <= 600
    assert "do it" in out and "## Contracts" in out
    assert "## Files" not in out or "[truncated]" in out


def test_required_sections_never_dropped() -> None:
    out = fit_sections(
        [
            Section("Task", "t\n" * 3000, priority=0, required=True),
            Section("Other", "o", priority=9),
        ],
        100,
    )
    assert out.count("t\n") == 3000


def test_within_budget_is_untouched() -> None:
    out = fit_sections([Section("A", "a"), Section("B", "b")], 1000)
    assert out == "## A\na\n\n## B\nb\n"


def test_developer_context_scoped_to_task() -> None:
    plan = Plan.model_validate(PLAN)
    task = plan.task("T2")
    ts = TaskState(id="T2", iterations=1, feedback="Fix the 404 handling")
    out = developer_context(
        task, plan, Design.model_validate(DESIGN), ts, "rules", "app/main.py", budget=4000
    )
    assert "id: T2" in out and "Fix the 404 handling" in out and "attempt 2" in out
    assert "Todo model" not in out  # other tasks are not included
    assert "GET/POST /todos" in out


def test_developer_context_respects_small_budget() -> None:
    plan = Plan.model_validate(PLAN)
    out = developer_context(
        plan.task("T1"),
        plan,
        Design.model_validate(DESIGN),
        TaskState(id="T1"),
        "rule\n" * 5000,
        "file\n" * 5000,
        budget=800,
    )
    assert estimate_tokens(out) <= 800 and "id: T1" in out
