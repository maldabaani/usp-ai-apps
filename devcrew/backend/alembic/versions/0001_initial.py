"""initial: runs and run_events

Revision ID: 0001
Revises:
Create Date: 2026-09-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("repo_target", sa.String(200), nullable=False),
        sa.Column("create_repo", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("pr_url", sa.String(500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("node", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_events_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_events")),
    )
    op.create_index("ix_run_events_run_id_id", "run_events", ["run_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_run_events_run_id_id", table_name="run_events")
    op.drop_table("run_events")
    op.drop_table("runs")
