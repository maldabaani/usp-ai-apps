"""Is code search working for a run? (Phase 16, BL-013) Agents fall back to reading files when
it is not, so a broken Chroma or embedding model is otherwise invisible."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from app.events.types import Event, EventType
from app.rag.service import RagService

INDEX_FAILED = "indexing failed"


class CodeSearchStatus(BaseModel):
    status: Literal["ok", "unavailable", "disabled", "pending"]
    detail: str | None = None


def code_search_status(
    rag: RagService | None, run_id: str, events: Sequence[Event]
) -> CodeSearchStatus:
    if rag is None:
        return CodeSearchStatus(
            status="disabled", detail="RAG_ENABLED=false: agents read files directly"
        )
    health = rag.health(run_id)
    if health is not None:
        ok, error = health
        return CodeSearchStatus(
            status="ok" if ok else "unavailable",
            detail=None if ok else f"{error} — agents read files directly until it recovers",
        )
    # after a backend restart: the last indexing outcome in the event log
    for event in reversed(events):
        if event.type is EventType.TOOL_RESULT and event.payload.get("tool") == "index_codebase":
            return CodeSearchStatus(status="ok")
        message = str(event.payload.get("message") or "")
        if event.type is EventType.ERROR and message.startswith(INDEX_FAILED):
            return CodeSearchStatus(status="unavailable", detail=message[:500])
    return CodeSearchStatus(status="pending", detail="the code is indexed after the scaffold")
