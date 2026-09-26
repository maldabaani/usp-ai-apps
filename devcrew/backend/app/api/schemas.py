"""API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.db.models import Run
from app.graph.interrupts import ResumeAction, ResumePayload
from app.graph.runner import PendingInterrupt

REPO_RE = r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$"


class CreateRunRequest(BaseModel):
    request: str = Field(min_length=10)  # max length: MAX_REQUEST_CHARS, checked in the route
    repo_target: str = Field(pattern=REPO_RE, description="owner/repo on GitHub")
    create_repo: bool = False
    target: Literal["new", "existing"] = Field(
        default="new", description="new: build a new project; existing: change this repository"
    )
    mode: Literal["full", "quick"] = Field(
        default="full",
        description="existing repositories: full (with the Architect) or quick (one approval)",
    )
    token_budget: int | None = Field(
        default=None, ge=0, description="stop and ask after this many tokens (0 = none)"
    )
    time_budget_min: int | None = Field(
        default=None, ge=0, description="stop and ask after this many working minutes (0 = none)"
    )

    def budget(self) -> dict[str, int] | None:
        """The run's own budget, or None for the settings' defaults."""
        if self.token_budget is None and self.time_budget_min is None:
            return None
        return {"tokens": self.token_budget or 0, "minutes": self.time_budget_min or 0}

    @field_validator("request")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ResumeRequest(BaseModel):
    action: ResumeAction
    feedback: str | None = None
    artifact: dict[str, Any] | None = None
    answer: str | None = None
    interrupt_id: str | None = None

    def payload(self) -> ResumePayload:
        return ResumePayload(
            action=self.action, feedback=self.feedback, artifact=self.artifact, answer=self.answer
        )


class RunSummary(BaseModel):
    id: str
    request: str
    repo_target: str
    create_repo: bool
    status: str
    pr_url: str | None
    error: str | None
    created_at: datetime | None
    updated_at: datetime | None
    busy: bool = False

    @classmethod
    def of(cls, run: Run, *, busy: bool = False) -> RunSummary:
        return cls(
            id=run.id,
            request=run.request,
            repo_target=run.repo_target,
            create_repo=run.create_repo,
            status=run.status,
            pr_url=run.pr_url,
            error=run.error,
            created_at=run.created_at,
            updated_at=run.updated_at,
            busy=busy,
        )


class PendingInput(BaseModel):
    interrupt_id: str
    kind: str
    title: str
    artifact: str | None = None
    allowed_actions: list[str]
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @classmethod
    def of(cls, p: PendingInterrupt) -> PendingInput:
        v = p.value
        return cls(
            interrupt_id=p.id,
            kind=v.get("kind", ""),
            title=v.get("title", ""),
            artifact=v.get("artifact"),
            allowed_actions=list(v.get("allowed_actions", [])),
            data=v.get("data") or {},
            error=v.get("error"),
        )


class RunDetail(RunSummary):
    target: str = "new"
    mode: str = "full"
    base_branch: str | None = None
    repo_info: dict[str, Any] | None = None
    gates: dict[str, Any] | None = None
    issue: dict[str, Any] | None = None
    followup: dict[str, Any] | None = None
    pause_requested: bool = False
    request_digest: str | None = None
    human_notes: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    design: dict[str, Any] | None = None
    tasks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    qa_log: list[dict[str, Any]] = Field(default_factory=list)
    integration: dict[str, Any] | None = None
    integration_branch: str | None = None
    wave: int = 0
    errors: list[str] = Field(default_factory=list)
    pending: list[PendingInput] = Field(default_factory=list)


class FileEntry(BaseModel):
    path: str
    size: int


class FileList(BaseModel):
    ref: str | None
    files: list[FileEntry]


class FileContent(BaseModel):
    ref: str
    path: str
    content: str
    truncated: bool = False
    binary: bool = False


class DiffResponse(BaseModel):
    task_id: str | None
    base: str
    head: str
    diff: str
    truncated: bool = False


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    task_id: str | None = Field(default=None, description="a task id, or null for the whole run")

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("the message is empty")
        return v


class MessageEdit(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("the message is empty")
        return v


class MessageOut(BaseModel):
    id: int
    task_id: str | None
    text: str
    status: str  # pending | delivered | applied | answered | expired | withdrawn
    action: str | None
    reply: str | None
    created_at: datetime | None
    updated_at: datetime | None


class PauseRequest(BaseModel):
    paused: bool


class PauseState(BaseModel):
    status: str
    pause_requested: bool
