"""App tables. Graph state lives in the LangGraph Postgres checkpointer; these tables hold
the run index (for listing) and the append-only event log (for SSE replay)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RunStatus(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"  # existing repository: clone, detect, index
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    DESIGNING = "designing"
    AWAITING_DESIGN_APPROVAL = "awaiting_design_approval"
    SCAFFOLDING = "scaffolding"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    CHECKING = "checking"  # quality gates
    WATCHING = "watching_pr"  # PR opened: following review comments, CI and conflicts
    AWAITING_FINAL_APPROVAL = "awaiting_final_approval"
    DELIVERING = "delivering"
    NEEDS_HUMAN = "needs_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    request: Mapped[str] = mapped_column(Text, nullable=False)
    repo_target: Mapped[str] = mapped_column(String(200), nullable=False)
    create_repo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Stored as plain string (not a PG enum) so adding statuses needs no type migration.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=RunStatus.PENDING)
    pr_url: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list[RunEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    # Global monotonically increasing id; doubles as the SSE event id (Last-Event-ID).
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    node: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped[Run] = relationship(back_populates="events")


class WatchedRepo(Base):
    """A repository polled for issues labelled `devcrew` / `devcrew:quick` (Phase 12)."""

    __tablename__ = "watched_repos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_s: Mapped[int] = mapped_column(BigInteger, nullable=False, default=300)
    # Reviewers without write access whose PR comments DevCrew should act on.
    extra_reviewers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IssueRun(Base):
    """A run started for a GitHub issue; `trigger` is the label event that started it."""

    __tablename__ = "issue_runs"
    __table_args__ = (
        Index("ux_issue_runs_trigger", "repo", "issue_number", "trigger", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trigger: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    comment_id: Mapped[int | None] = mapped_column(BigInteger)
    comment_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
