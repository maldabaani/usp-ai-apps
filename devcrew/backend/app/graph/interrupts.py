"""Interrupt requests (graph -> human) and resume payloads (human -> graph)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from langgraph.types import interrupt
from pydantic import BaseModel, Field, ValidationError, model_validator


class InterruptKind(StrEnum):
    APPROVAL = "approval"  # plan / design / final
    QUESTION = "question"  # ask_human
    ESCALATION = "escalation"  # iteration limit or unrecoverable agent error
    WATCH = "watch"  # waiting for GitHub activity on the PR (resumed by the poller)


class ResumeAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    ANSWER = "answer"
    UPDATE = "update"  # new PR activity delivered by the poller (artifact = activity)


class ResumePayload(BaseModel):
    action: ResumeAction
    feedback: str | None = None
    artifact: dict[str, Any] | None = None
    answer: str | None = None

    @model_validator(mode="after")
    def _required_fields(self) -> Self:
        if self.action is ResumeAction.REJECT and not (self.feedback or "").strip():
            raise ValueError("reject requires feedback")
        if self.action is ResumeAction.EDIT and self.artifact is None:
            raise ValueError("edit requires artifact")
        if self.action is ResumeAction.ANSWER and not (self.answer or "").strip():
            raise ValueError("answer requires answer text")
        if self.action is ResumeAction.UPDATE and self.artifact is None:
            raise ValueError("update requires artifact (the PR activity)")
        return self


class InterruptRequest(BaseModel):
    kind: InterruptKind
    title: str
    artifact: str | None = None  # plan | design | final
    allowed_actions: list[ResumeAction]
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None  # why a previous resume payload was rejected


def request_input(request: InterruptRequest) -> ResumePayload:
    """interrupt() until the human sends a payload that is valid for this request.

    Invalid payloads re-interrupt with `error` set (each interrupt() call in a node has its own
    resume slot, so the loop is replay-safe).
    """
    current = request
    while True:
        raw = interrupt(current.model_dump(mode="json"))
        try:
            payload = ResumePayload.model_validate(raw)
        except ValidationError as exc:
            current = request.model_copy(update={"error": f"invalid resume payload: {exc}"})
            continue
        if payload.action not in request.allowed_actions:
            allowed = ", ".join(a.value for a in request.allowed_actions)
            current = request.model_copy(
                update={"error": f"action '{payload.action}' not allowed here; use {allowed}"}
            )
            continue
        return payload
