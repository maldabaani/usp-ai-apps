"""Reviewer scope: a task is not asked for another task's work (real run, qwen2.5:32b)."""

from __future__ import annotations

from devcrew.graph.context_builder import reviewer_context
from devcrew.graph.nodes.reviewer import drop_out_of_scope
from devcrew.graph.state import Design, Plan, ReviewIssue, ReviewResult
from tests.devcrew.graph_harness import DESIGN


def plan() -> Plan:
    def task(tid: str, title: str, files: list[str], deps: list[str]) -> dict[str, object]:
        return {
            "id": tid,
            "title": title,
            "description": title,
            "target_files": files,
            "depends_on": deps,
            "stack": "python",
            "story_ids": ["US1"],
        }

    return Plan.model_validate(
        {
            "summary": "Bookmarks API",
            "user_stories": [
                {
                    "id": "US1",
                    "story": "Save bookmarks",
                    "acceptance_criteria": ["every endpoint is tested"],
                }
            ],
            "tasks": [
                task("T1", "Define Bookmark Model", ["app/schemas/bookmark.py"], []),
                task("T2", "Bookmark service", ["app/services/bookmark_service.py"], ["T1"]),
                task("T3", "Bookmarks router", ["app/routers/bookmarks.py"], ["T2"]),
                task("T4", "Endpoint tests", ["tests/test_bookmarks_endpoints.py"], ["T3"]),
            ],
        }
    )


DIFF = (
    "diff --git a/models/bookmark.py b/models/bookmark.py\n"
    "--- /dev/null\n+++ b/models/bookmark.py\n"
)


def issue(file: str, message: str, severity: str = "major") -> ReviewIssue:
    return ReviewIssue(file=file, severity=severity, message=message)


def test_issues_about_other_tasks_files_do_not_block() -> None:
    p = plan()
    review = ReviewResult(
        decision="changes_requested",
        summary="several files required by the design are missing",
        issues=[
            issue("app/schemas/bookmark.py", "The BookmarkSchema is missing."),
            issue("app/services/bookmark_service.py", "The BookmarkService is missing."),
            issue("app/routers/bookmarks.py", "The Bookmarks Router is missing."),
            issue("tests/test_bookmarks_endpoints.py", "The endpoint tests are missing."),
            # the first real review pinned everything on the changed file; the path is in the text
            issue(
                "models/bookmark.py",
                "The create_bookmark function in app/services/bookmark_service.py is missing.",
            ),
        ],
    )
    scoped = drop_out_of_scope(review, p.tasks[0], p, DIFF)
    severities = [(i.file, i.severity) for i in scoped.issues]
    assert severities[0] == ("app/schemas/bookmark.py", "major")  # T1's own file: still blocking
    assert [s for _, s in severities[1:]] == ["info"] * 4
    assert scoped.issues[1].message.startswith("[delivered by T2]")
    assert scoped.issues[3].message.startswith("[delivered by T4]")
    assert scoped.decision == "changes_requested"


def test_a_review_that_only_asks_for_other_tasks_work_approves() -> None:
    p = plan()
    review = ReviewResult(
        decision="changes_requested",
        issues=[issue("app/routers/bookmarks.py", "The router is missing.")],
    )
    scoped = drop_out_of_scope(review, p.tasks[0], p, DIFF)
    assert scoped.decision == "approve" and scoped.issues[0].severity == "info"


def test_a_task_that_changes_another_tasks_file_is_still_reviewed_on_it() -> None:
    p = plan()
    diff = DIFF + "+++ b/app/routers/bookmarks.py\n"
    review = ReviewResult(
        decision="changes_requested",
        issues=[issue("app/routers/bookmarks.py", "This change breaks the router.", "blocker")],
    )
    assert drop_out_of_scope(review, p.tasks[0], p, diff).issues[0].severity == "blocker"


def test_the_reviewer_sees_the_other_tasks() -> None:
    p = plan()
    text = reviewer_context(p.tasks[0], p, Design.model_validate(DESIGN), "", DIFF, 8000)
    assert "Other tasks of the plan" in text
    assert "T3 Bookmarks router (files: app/routers/bookmarks.py)" in text
    assert "T1 Define Bookmark Model (files" not in text


def test_qa_may_not_write_another_tasks_test_file() -> None:
    """Real run: QA of the schema task wrote the endpoint tests (T4's file), which could never
    pass before the endpoints exist."""
    from devcrew.graph.context_builder import other_tasks_files
    from devcrew.graph.nodes.qa import qa_may_write

    p = plan()
    others = other_tasks_files(p, p.tasks[0])
    assert others["tests/test_bookmarks_endpoints.py"] == "T4"
    assert "app/schemas/bookmark.py" not in others
    assert not qa_may_write("python", others, "tests/test_bookmarks_endpoints.py")
    assert qa_may_write("python", others, "tests/test_bookmark_schema.py")
    assert not qa_may_write("python", others, "app/schemas/bookmark.py")  # not a test file


def test_developer_and_qa_see_the_other_tasks() -> None:
    from devcrew.graph.context_builder import developer_context, qa_context
    from devcrew.graph.state import TaskState

    p = plan()
    design = Design.model_validate(DESIGN)
    dev = developer_context(p.tasks[0], p, design, TaskState(id="T1"), "", "", 8000)
    assert "Other tasks of the plan" in dev and "T4 Endpoint tests" in dev
    qa = qa_context(p.tasks[0], p, design, ["app/schemas/bookmark.py"], "pytest -q", "", 8000)
    assert "do NOT test their code" in qa and "T3 Bookmarks router" in qa
