"""Graph state and the structured artifacts agents produce.

State is kept JSON-only (artifacts stored via model_dump(mode="json"), messages via
messages_to_dict) so checkpoints survive restarts and library upgrades. Use the typed
accessors below to read artifacts back as models.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

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
    def _valid_dag(self, info: ValidationInfo) -> Self:
        allowed: set[str] | None = (info.context or {}).get("allowed_stacks")
        if allowed:
            wrong = sorted({t.stack for t in self.tasks} - allowed)
            if wrong:
                raise ValueError(
                    f"tasks use {wrong} but this repository only has {sorted(allowed)} projects"
                )
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


class Component(BaseModel):
    """One sub-project of a mixed-stack design, scaffolded from its own template."""

    template_id: str
    path: str = Field(description="Sub-directory, e.g. backend or frontend.")

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        p = _check_relative_path(v).strip("/")
        if p in ("", "."):
            raise ValueError("component path must be a sub-directory, not the project root")
        return p


class PlanAssessment(BaseModel):
    """The Architect's review of the approved plan (advisory: the plan is not changed)."""

    concerns: list[str] = Field(
        default_factory=list, description="Risks, gaps or ambiguities in the plan."
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Assumptions the design makes where the plan is silent."
    )
    suggested_changes: list[str] = Field(
        default_factory=list,
        description="Changes to the plan you recommend (the human decides whether to apply them).",
    )


class ExistingProject(BaseModel):
    """A project detected in an existing repository (filled by DevCrew, not by the model)."""

    stack: Literal["python", "java", "angular"]
    path: str = "."
    install_cmd: str
    build_cmd: str = ""
    test_cmd: str
    coverage_cmd: str | None = None

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        p = _check_relative_path(v).strip("/")
        return p or "."


class ExistingDesignDraft(BaseModel):
    """What the Architect writes for an existing repository (layout fields are filled in)."""

    project_structure: list[str] = Field(description="Existing and new paths involved.")
    modules: list[ModuleContract] = Field(min_length=1)
    key_decisions: list[str] = Field(default_factory=list)
    design_doc: str = Field(description="Markdown design document for the change.")
    plan_assessment: PlanAssessment | None = Field(
        default=None, description="Your assessment of the plan you are designing for."
    )


class Design(BaseModel):
    stack: Stack
    template_id: str
    components: list[Component] = Field(
        default_factory=list,
        description="Only for stack=mixed: one template per sub-project (e.g. backend/, "
        "frontend/). Leave empty for single-stack projects.",
    )
    project_structure: list[str] = Field(description="Key paths of the final project.")
    modules: list[ModuleContract] = Field(min_length=1)
    key_decisions: list[str] = Field(default_factory=list)
    design_doc: str = Field(description="Markdown design document.")
    plan_assessment: PlanAssessment | None = Field(
        default=None, description="Your assessment of the plan you are designing for."
    )
    existing_projects: list[ExistingProject] = Field(
        default_factory=list,
        description="Filled in by DevCrew for existing repositories. Always leave empty.",
    )

    @model_validator(mode="after")
    def _layout_is_valid(self, info: ValidationInfo) -> Self:
        context = info.context or {}
        if self.existing_projects:
            # Existing repository: the layout comes from detection, not from templates.
            found: list[str] = [p.stack for p in self.existing_projects]
            paths = [p.path for p in self.existing_projects]
            if len(set(found)) != len(found) or len(set(paths)) != len(paths):
                raise ValueError("existing projects must have distinct stacks and paths")
            expected = Stack.MIXED if len(found) > 1 else Stack(found[0])
            if self.stack is not expected:
                raise ValueError(f"stack must be {expected.value} for this repository")
            wanted: set[str] | None = context.get("task_stacks")
            if wanted and not wanted <= set(found):
                raise ValueError(
                    f"the plan has {sorted(wanted - set(found))} tasks but the repository "
                    f"only contains {sorted(found)} projects"
                )
            return self
        if self.stack is Stack.MIXED:
            if len(self.components) < 2:
                raise ValueError("stack 'mixed' needs at least two components (template + path)")
            paths = [c.path for c in self.components]
            if len(set(paths)) != len(paths):
                raise ValueError("component paths must be distinct")
            if self.template_id not in {c.template_id for c in self.components}:
                raise ValueError("template_id must be one of the components' template ids")
        elif self.components:
            raise ValueError("components are only used when stack is 'mixed'")

        templates: Mapping[str, str] | None = context.get("templates")
        if templates is not None:
            for template_id in {self.template_id, *(c.template_id for c in self.components)}:
                if template_id not in templates:
                    raise ValueError(
                        f"template_id '{template_id}' is unknown; choose one of {sorted(templates)}"
                    )
            stacks = self.stacks(templates)
            if self.stack is not Stack.MIXED and stacks != [self.stack.value]:
                raise ValueError(
                    f"template '{self.template_id}' is for {stacks[0]}, but stack is {self.stack}"
                )
            if self.stack is Stack.MIXED and len(set(stacks)) != len(stacks):
                raise ValueError("each component must use a different stack")
            task_stacks: set[str] | None = context.get("task_stacks")
            if task_stacks and not task_stacks <= set(stacks):
                missing = sorted(task_stacks - set(stacks))
                raise ValueError(
                    f"the plan has {missing} tasks but the design provides no template for them; "
                    "use stack 'mixed' with one component per stack"
                )
        return self

    def stacks(self, templates: Mapping[str, str]) -> list[str]:
        if self.stack is Stack.MIXED:
            return [templates[c.template_id] for c in self.components]
        return [templates[self.template_id]]


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
    SPLIT = "split"  # replaced by subtasks (Coordinator)
    CANCELLED = "cancelled"  # dropped on the human's request before it started

    @property
    def is_final(self) -> bool:
        return self in (
            TaskStatus.MERGED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.SPLIT,
            TaskStatus.CANCELLED,
        )


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
    worktree: str | None = None
    wave: int | None = None  # scheduler wave that dispatched the task (UI lanes)
    lane: int | None = None  # position within the wave
    conflict_files: list[str] = Field(default_factory=list)  # unresolved merge conflicts
    conflict_rounds: int = 0
    coordinator_actions: int = 0  # automatic Coordinator decisions taken for this task
    reset_branch: bool = False  # replanned: restart from the integration branch
    # PR follow-up: merge this ref (the PR base) into the task branch first; the task result
    # is then fast-forwarded (not squashed) so the merge commit keeps the base as a parent.
    merge_from: str | None = None


class QAEntry(BaseModel):
    id: str
    task_id: str | None = None
    asker: str  # role
    target: str  # "human" or a role
    question: str
    answer: str


class AgentAnswer(BaseModel):
    """One-shot answer from another role (ask_agent)."""

    answer: str = Field(description="Direct answer the asking developer can act on.")
    needs_human: bool = Field(
        default=False, description="True only if this needs a decision from the human user."
    )
    reason: str = Field(default="", description="Why the human is needed (if needs_human).")


class RevisedTask(BaseModel):
    title: str
    description: str
    target_files: list[str] = Field(default_factory=list)

    @field_validator("target_files")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return [_check_relative_path(p) for p in v]


CoordinatorAction = Literal["retry", "replan", "split", "escalate"]


class CoordinatorDecision(BaseModel):
    """The Coordinator's exception-handling decision (LLM, validated)."""

    action: CoordinatorAction
    reason: str = Field(description="Why this action fixes the problem.")
    guidance: str = Field(default="", description="Concrete instructions for the next attempt.")
    revised_task: RevisedTask | None = Field(default=None, description="Required for replan.")
    subtasks: list[PlanTask] = Field(default_factory=list, description="Required for split.")
    question_for_human: str = Field(default="", description="Required for escalate.")

    @model_validator(mode="after")
    def _consistent(self, info: ValidationInfo) -> Self:
        context = info.context or {}
        allowed: Sequence[str] | None = context.get("allowed_actions")
        if allowed is not None and self.action not in allowed:
            raise ValueError(f"action must be one of {list(allowed)}")
        if self.action == "retry" and not self.guidance.strip():
            raise ValueError("retry needs guidance")
        if self.action == "replan" and self.revised_task is None:
            raise ValueError("replan needs revised_task")
        if self.action == "escalate" and not self.question_for_human.strip():
            raise ValueError("escalate needs question_for_human")
        if self.action == "split":
            if len(self.subtasks) < 2:
                raise ValueError("split needs at least two subtasks")
            existing: set[str] = set(context.get("existing_ids", ()))
            ids = [t.id for t in self.subtasks]
            clash = sorted((set(ids) & existing) | {i for i in ids if ids.count(i) > 1})
            if clash:
                raise ValueError(f"subtask ids must be new and unique; clashing: {clash}")
            allowed_deps = set(ids) | set(context.get("task_depends_on", ()))
            for t in self.subtasks:
                bad = sorted(set(t.depends_on) - allowed_deps)
                if bad:
                    raise ValueError(
                        f"subtask {t.id} may only depend on sibling subtasks or the original "
                        f"task's dependencies; invalid: {bad}"
                    )
        return self


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
    status: str  # RunStatus value (plain str: checkpoints hold JSON-compatible data only)

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
    final_approved: bool  # set only by the final approval gate; delivery refuses without it
    delivery_error: str | None

    # Phase 5: waves of parallel tasks and Coordinator plan changes (replan/split), applied by
    # the scheduler in order; `plan_changes_applied` counts how many were applied already.
    wave: int
    plan_changes: Annotated[list[dict[str, Any]], operator.add]
    plan_changes_applied: int
    coordinator_retries: Annotated[dict[str, Any], merge_dicts]

    # Phase 11: existing repositories and quality gates.
    target: str  # "new" | "existing"
    mode: str  # "full" | "quick" (existing repositories only)
    base_branch: str  # PR base: "main" for new projects, the default branch otherwise
    repo_info: dict[str, Any] | None  # detected projects + repository summary
    gate_baseline: dict[str, Any] | None  # coverage / known vulnerabilities on the base branch
    gates: dict[str, Any] | None  # latest GateReport (integration)
    gate_fix_rounds: int

    # Phase 12: GitHub issue that started the run, and PR follow-up rounds.
    issue: dict[str, Any] | None  # {"repo", "number", "url", "title"}
    followup: dict[str, Any] | None  # round, handled keys, pending replies, ignored authors
    followup_items: list[dict[str, Any]] | None  # activity delivered by the poller
    followup_triage: list[dict[str, Any]] | None
    followup_active: bool  # a follow-up round is being implemented

    # Phase 13: steering. Notes from the human's chat messages (given to every agent from then
    # on) and the ids of run-level messages already applied (state is the source of truth, so a
    # node that re-runs after a crash applies each message exactly once).
    human_notes: list[dict[str, Any]]
    steer_applied: list[int]

    # Phase 14: budgets. `budget` is the run's own {"tokens", "minutes"} (None: the settings'
    # defaults); `budget_limit` is the raised limit after "continue"; `wave_checked` is the last
    # wave whose merged result was tested; `wave_fixes` counts the fix tasks those tests added.
    budget: dict[str, int] | None
    budget_limit: dict[str, int] | None
    wave_checked: int
    wave_fixes: int


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
    escalation_kind: str | None  # iteration_limit | agent_error | merge_conflict
    escalation_node: str | None  # node to retry
    escalation_question: str | None  # what the escalate node asks the human
    plan_changes: Annotated[list[dict[str, Any]], operator.add]
    human_notes: list[dict[str, Any]]  # run-level notes from the human's chat (Phase 13)


class TaskWorkerOutput(TypedDict, total=False):
    tasks: Annotated[dict[str, dict[str, Any]], merge_dicts]
    qa_log: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
    plan_changes: Annotated[list[dict[str, Any]], operator.add]


def get_plan(state: Mapping[str, Any]) -> Plan:
    return Plan.model_validate(state["plan"])


def get_design(state: Mapping[str, Any]) -> Design:
    return Design.model_validate(state["design"])


def get_task_state(state: Mapping[str, Any], task_id: str) -> TaskState:
    return TaskState.model_validate(state["tasks"][task_id])


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
