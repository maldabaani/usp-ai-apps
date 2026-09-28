"""A contradicting task description (Mac run: "return a dictionary", after a review misread
PY-003) must not win over the human's message, the rule text, or a clean review."""

from __future__ import annotations

from pathlib import Path

from app.graph.context_builder import developer_context
from app.graph.nodes.reviewer import merge_duplicate_issues, render_feedback
from app.graph.state import Design, Plan, ReviewIssue, ReviewResult, TaskState
from app.tools.catalog import RulesCatalog
from tests.graph_harness import DESIGN, PLAN

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"


def test_human_instructions_come_last_and_override() -> None:
    plan = Plan.model_validate(PLAN)
    out = developer_context(
        plan.task("T2"),
        plan,
        Design.model_validate(DESIGN),
        TaskState(id="T2", iterations=1, feedback="Return a dictionary"),
        "rules",
        "app/main.py",
        budget=4000,
        notes=["(for this task) Return the Bookmark Pydantic model, not a dict"],
    )
    last = out.rsplit("## ", 1)[1]
    assert last.startswith("Instructions from the human (they OVERRIDE")
    assert "Return the Bookmark Pydantic model" in last


def issue(line: int, message: str, rule: str | None = "PY-021") -> ReviewIssue:
    return ReviewIssue(
        file="app/bookmarks.py", line=line, severity="major", message=message, rule_ref=rule
    )


def test_repeated_issues_are_merged_and_rules_quoted() -> None:
    review = ReviewResult(
        decision="changes_requested",
        summary="Fix the error handling.",
        issues=[
            issue(10, "Catch specific exceptions."),
            issue(12, "Catch specific exceptions."),
            issue(14, "Catch specific exceptions."),
            issue(17, "Return a Pydantic model.", "PY-003"),
        ],
    )
    merged = merge_duplicate_issues(review)
    assert len(merged.issues) == 2
    assert merged.issues[0].message == "Catch specific exceptions. (also lines 12, 14)"

    texts = RulesCatalog(RULES_DIR).rule_texts(["python"])
    assert texts["PY-003"].startswith("Request/response bodies are Pydantic v2 models")
    feedback = render_feedback(merged, texts)
    assert "The cited rules say" in feedback
    assert "- PY-003: Request/response bodies are Pydantic v2 models" in feedback
    assert "- PY-021: No bare `except:`" in feedback
