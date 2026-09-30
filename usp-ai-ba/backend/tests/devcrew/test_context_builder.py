from __future__ import annotations

from devcrew.graph.context_builder import Section, developer_context, fit_sections
from devcrew.graph.state import Design, Plan, TaskState
from devcrew.llm.tokens import estimate_tokens
from tests.devcrew.graph_harness import DESIGN, PLAN


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
    # other tasks appear only as a boundary (title and files), not with their descriptions
    assert "- T1 Todo model (files: app/schemas/todo.py)" in out
    assert "Pydantic schemas" not in out
    assert "GET/POST /todos" in out


def test_developer_context_lists_files_already_touched() -> None:
    # A retry's own tool-calling conversation starts empty (see developer.py); this bounded list
    # is the only thing telling it what an earlier attempt already wrote.
    plan = Plan.model_validate(PLAN)
    task = plan.task("T2")
    ts = TaskState(id="T2", iterations=1, feedback="Fix the 404 handling")
    out = developer_context(
        task,
        plan,
        Design.model_validate(DESIGN),
        ts,
        "rules",
        "app/main.py",
        budget=4000,
        touched_files=["app/routers/todos.py", "app/schemas/todo.py"],
    )
    assert "Files you already wrote for this task" in out
    assert "app/routers/todos.py" in out and "app/schemas/todo.py" in out


def test_developer_context_bounds_a_long_touched_files_list() -> None:
    plan = Plan.model_validate(PLAN)
    task = plan.task("T2")
    files = [f"app/f{i}.py" for i in range(50)]
    out = developer_context(
        task,
        plan,
        Design.model_validate(DESIGN),
        TaskState(id="T2", iterations=1),
        "rules",
        "app/main.py",
        budget=4000,
        touched_files=files,
    )
    assert "app/f0.py" in out and "app/f39.py" in out
    assert "app/f40.py" not in out
    assert "+10 more" in out


def test_developer_context_omits_touched_files_section_on_a_first_attempt() -> None:
    plan = Plan.model_validate(PLAN)
    out = developer_context(
        plan.task("T1"),
        plan,
        Design.model_validate(DESIGN),
        TaskState(id="T1"),
        "rules",
        "app/main.py",
        budget=4000,
    )
    assert "Files you already wrote for this task" not in out


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


def test_retrieved_code_is_truncated_to_fit_budget() -> None:
    plan = Plan.model_validate(PLAN)
    chunk = "--- app/x.py:1-40  (def f)\n" + "code line\n" * 40
    out = developer_context(
        plan.task("T2"),
        plan,
        Design.model_validate(DESIGN),
        TaskState(id="T2"),
        "rules",
        "app/main.py",
        budget=900,
        related_code="\n\n".join([chunk] * 50),
    )
    assert estimate_tokens(out) <= 900
    assert "Relevant existing code" in out and "[truncated]" in out
    assert "id: T2" in out
