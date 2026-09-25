"""Graph state and the structured artifacts agents produce.

State is kept JSON-only (artifacts stored via model_dump(mode="json"), messages via
messages_to_dict) so checkpoints survive restarts and library upgrades. Use the typed
accessors below to read artifacts back as models.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.db.models import RunStatus

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")

TaskStack = Literal["python", "java", "angular"]


class Stack(StrEnum):
    PYTHON = "python"
    JAVA = "java"
    ANGULAR = "angular"
    MIXED = "mixed"


def _check_relative_path(path: str) -> str:
    p = path.strip().replace("\\", "/")
    if not p or p.startswith("/") or ".." in p.split("/"):
        raise ValueError(f"'{path}' must be a relative path inside the project")
    return p


# --------------------------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------------------------
class UserStory(BaseModel):
    id: str = Field(description="e.g. US1")
    story: str = Field(description="As a <user>, I want <goal> so that <benefit>.")
    acceptance_criteria: list[str] = Field(min_length=1)


class PlanTask(BaseModel):
    id: str = Field(description="Short unique id, e.g. T1")
    title: str
    description: str
    target_files: list[str] = Field(description="Files this task creates or edits.")
    depends_on: list[str] = Field(default_factory=list)
    stack: TaskStack
    story_ids: list[str] = Field(default_factory=list, description="User stories covered.")

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not TASK_ID_RE.match(v):
            raise ValueError("id must be 1-40 chars of letters, digits, '_' or '-'")
        return v

    @field_validator("target_files")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return [_check_relative_path(p) for p in v]


class Plan(BaseModel):
    summary: str
    user_stories: list[UserStory] = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_dag(self) -> Self:
        ids = [t.id for t in self.tasks]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate task ids: {dupes}")
        known = set(ids)
        story_ids = {s.id for s in self.user_stories}
        for t in self.tasks:
            missing = [d for d in t.depends_on if d not in known]
            if missing:
                raise ValueError(f"task {t.id} depends on unknown tasks {missing}")
            if t.id in t.depends_on:
                raise ValueError(f"task {t.id} depends on itself")
            unknown_stories = [s for s in t.story_ids if s not in story_ids]
            if unknown_stories:
                raise ValueError(f"task {t.id} references unknown stories {unknown_stories}")
        self.layers()  # raises on cycles
        return self

    def layers(self) -> list[list[str]]:
        """Topological layers: tasks in the same layer can run in parallel."""
        remaining = {t.id: set(t.depends_on) for t in self.tasks}
        layers: list[list[str]] = []
        done: set[str] = set()
        while remaining:
            ready = sorted(tid for tid, deps in remaining.items() if deps <= done)
            if not ready:
                raise ValueError(f"task dependencies contain a cycle among {sorted(remaining)}")
            layers.append(ready)
            done.update(ready)
            for tid in ready:
                del remaining[tid]
        return layers

    def task(self, task_id: str) -> PlanTask:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise KeyError(task_id)


# --------------------------------------------------------------------------------------------
# Architect
# --------------------------------------------------------------------------------------------
class ModuleContract(BaseModel):
    name: str
    path: str = Field(description="Directory or file the module lives in.")
    responsibility: str
    interface: str = Field(description="Public signatures, endpoints or DTOs other modules use.")


class Design(BaseModel):
    stack: Stack
    template_id: str
    project_structure: list[str] = Field(description="Key paths of the final project.")
    modules: list[ModuleContract] = Field(min_length=1)
    key_decisions: list[str] = Field(default_factory=list)
    design_doc: str = Field(description="Markdown design document.")

    @model_validator(mode="after")
    def _template_matches(self, info: ValidationInfo) -> Self:
        templates: Mapping[str, str] | None = (info.context or {}).get("templates")
        if templates is None:
            return self
        if self.template_id not in templates:
            raise ValueError(
                f"template_id '{self.template_id}' is unknown; choose one of {sorted(templates)}"
            )
        template_stack = templates[self.template_id]
        if self.stack is not Stack.MIXED and template_stack != self.stack.value:
            raise ValueError(
                f"template '{self.template_id}' is for {template_stack}, but stack is {self.stack}"
            )
        return self


# --------------------------------------------------------------------------------------------
# Reviewer / QA
# --------------------------------------------------------------------------------------------
Severity = Literal["blocker", "major", "minor", "info"]


class ReviewIssue(BaseModel):
    file: str
    line: int | None = None
    severity: Severity
    message: str
    rule_ref: str | None = Field(default=None, description="Rule id such as PY-003, if any.")


class ReviewResult(BaseModel):
    decision: Literal["approve", "changes_requested"]
    summary: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self, info: ValidationInfo) -> Self:
        blocking = [i for i in self.issues if i.severity in ("blocker", "major")]
        if self.decision == "approve" and blocking:
            raise ValueError("decision cannot be 'approve' while blocker/major issues exist")
        if self.decision == "changes_requested" and not self.issues:
            raise ValueError("'changes_requested' needs at least one issue")
        rule_ids: set[str] | None = (info.context or {}).get("rule_ids")
        if rule_ids:
            unknown = sorted({i.rule_ref for i in self.issues if i.rule_ref} - rule_ids)
            if unknown:
                raise ValueError(f"unknown rule_ref values {unknown}; cite ids from the rules")
        return self


class QAReport(BaseModel):
    """What the QA model reports about a test run; `passed` comes from the exit code."""

    failed: list[str] = Field(default_factory=list, description="Names of failing tests.")
    summary: str = Field(description="Short diagnosis the Developer can act on.")


class TestResult(BaseModel):
    ran: bool
    passed: bool
    failed: list[str] = Field(default_factory=list)
    logs_excerpt: str = ""
    command: str | None = None


# --------------------------------------------------------------------------------------------
# Run bookkeeping
# --------------------------------------------------------------------------------------------
class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    TESTING = "testing"
    MERGED = "merged"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"
    BLOCKED = "blocked"

    @property
    def is_final(self) -> bool:
        return self in (TaskStatus.MERGED, TaskStatus.FAILED, TaskStatus.BLOCKED)


class TaskState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str
    status: TaskStatus = TaskStatus.PENDING
    branch: str | None = None
    iterations: int = 0
    review: ReviewResult | None = None
    test_results: TestResult | None = None
    feedback: str | None = None  # what the Developer must address next
    commit: str | None = None
    error: str | None = None


class QAEntry(BaseModel):
    id: str
    task_id: str | None = None
    asker: str  # role
    target: str  # "human" or a role
    question: str
    answer: str


class PendingQuestion(BaseModel):
    id: str
    role: str
    task_id: str | None = None
    question: str
    tool_call_id: str


# --------------------------------------------------------------------------------------------
# Reducers and state schemas
# --------------------------------------------------------------------------------------------
def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow merge by key; a value of None deletes the key."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


class RunState(TypedDict, total=False):
    run_id: str
    request: str
    repo_target: str
    create_repo: bool
    status: RunStatus

    plan: dict[str, Any] | None
    plan_feedback: str | None
    design: dict[str, Any] | None
    design_feedback: str | None

    workspace: str
    integration_branch: str
    tasks: Annotated[dict[str, dict[str, Any]], merge_dicts]
    integration: dict[str, Any] | None
    final_feedback: str | None
    followups: int

    qa_log: Annotated[list[dict[str, Any]], operator.add]
    pending_question: dict[str, Any] | None
    # Saved agent transcripts (serialized messages) for resuming after ask_human.
    scratch: Annotated[dict[str, list[dict[str, Any]]], merge_dicts]
    errors: Annotated[list[str], operator.add]
    escalation: dict[str, Any] | None

    pr_url: str | None


class TaskWorkerState(TypedDict, total=False):
    run_id: str
    task: dict[str, Any]
    plan: dict[str, Any]
    design: dict[str, Any]
    workspace: str
    integration_branch: str
    tasks: Annotated[dict[str, dict[str, Any]], merge_dicts]
    qa_log: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
    task_scratch: Annotated[dict[str, list[dict[str, Any]]], merge_dicts]
    task_pending_question: dict[str, Any] | None
    escalation_reason: str | None


class TaskWorkerOutput(TypedDict, total=False):
    tasks: Annotated[dict[str, dict[str, Any]], merge_dicts]
    qa_log: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]


def get_plan(state: Mapping[str, Any]) -> Plan:
    return Plan.model_validate(state["plan"])


def get_design(state: Mapping[str, Any]) -> Design:
    return Design.model_validate(state["design"])


def get_task_state(state: Mapping[str, Any], task_id: str) -> TaskState:
    return TaskState.model_validate(state["tasks"][task_id])


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
