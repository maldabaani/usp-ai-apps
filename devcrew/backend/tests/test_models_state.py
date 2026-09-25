from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.graph.interrupts import ResumePayload
from app.graph.state import Design, Plan, ReviewResult
from tests.graph_harness import DESIGN, PLAN


def _plan(tasks: list[dict[str, object]]) -> dict[str, object]:
    return {**PLAN, "tasks": tasks}


def _task(tid: str, deps: list[str] | None = None, **kw: object) -> dict[str, object]:
    return {
        "id": tid,
        "title": tid,
        "description": "d",
        "target_files": [f"{tid}.py"],
        "depends_on": deps or [],
        "stack": "python",
        **kw,
    }


def test_layers_expose_parallelism() -> None:
    plan = Plan.model_validate(
        _plan([_task("A"), _task("B"), _task("C", ["A", "B"]), _task("D", ["A"])])
    )
    assert plan.layers() == [["A", "B"], ["C", "D"]]


@pytest.mark.parametrize(
    ("tasks", "message"),
    [
        ([_task("A", ["B"]), _task("B", ["A"])], "cycle"),
        ([_task("A", ["Z"])], "unknown tasks"),
        ([_task("A"), _task("A")], "duplicate"),
        ([_task("A", ["A"])], "itself"),
        ([_task("A", story_ids=["US9"])], "unknown stories"),
        ([_task("bad id!")], "letters, digits"),
        ([{**_task("A"), "target_files": ["../etc/passwd"]}], "relative path"),
        ([{**_task("A"), "stack": "rust"}], "python"),
    ],
)
def test_invalid_plans(tasks: list[dict[str, object]], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Plan.model_validate(_plan(tasks))


def test_design_template_validation() -> None:
    ctx = {"templates": {"python-fastapi": "python", "java-spring-boot": "java"}}
    Design.model_validate(DESIGN, context=ctx)
    with pytest.raises(ValidationError, match="is for java"):
        Design.model_validate({**DESIGN, "template_id": "java-spring-boot"}, context=ctx)
    Design.model_validate(
        {**DESIGN, "stack": "mixed", "template_id": "java-spring-boot"}, context=ctx
    )


def test_review_consistency() -> None:
    issue = {"file": "a.py", "severity": "blocker", "message": "m"}
    with pytest.raises(ValidationError, match="cannot be 'approve'"):
        ReviewResult.model_validate({"decision": "approve", "issues": [issue]})
    with pytest.raises(ValidationError, match="at least one issue"):
        ReviewResult.model_validate({"decision": "changes_requested"})
    minor = {**issue, "severity": "minor"}
    assert ReviewResult.model_validate({"decision": "approve", "issues": [minor]})


def test_resume_payload_requirements() -> None:
    with pytest.raises(ValidationError):
        ResumePayload.model_validate({"action": "reject"})
    with pytest.raises(ValidationError):
        ResumePayload.model_validate({"action": "edit"})
    with pytest.raises(ValidationError):
        ResumePayload.model_validate({"action": "answer", "answer": "  "})
    assert ResumePayload.model_validate({"action": "approve"})
