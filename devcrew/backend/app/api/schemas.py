"""API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.db.models import Run
from app.graph.interrupts import ResumeAction, ResumePayload
from app.graph.runner import PendingInterrupt

REPO_RE = r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$"


class CreateRunRequest(BaseModel):
    request: str = Field(min_length=10, max_length=20_000)
    repo_target: str = Field(pattern=REPO_RE, description="owner/repo on GitHub")
    create_repo: bool = False

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
