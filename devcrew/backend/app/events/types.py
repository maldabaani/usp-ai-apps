from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    NODE_STARTED = "node_started"
    NODE_FINISHED = "node_finished"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    QUESTION = "question"
    ANSWER = "answer"
    MERGE = "merge"
    ERROR = "error"
    STATUS = "status"  # run status transition
    AWAITING_INPUT = "awaiting_input"  # graph interrupted, waiting for the human
    MESSAGE = "message"  # a chat message from the human and what was done with it


class EventIn(BaseModel):
    """An event before persistence (no id yet)."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    type: EventType
    node: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Event(EventIn):
    """A persisted event. `id` is monotonically increasing and used as the SSE event id."""

    id: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
