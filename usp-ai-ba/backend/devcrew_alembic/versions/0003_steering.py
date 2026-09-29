"""steering: run_messages and runs.pause_requested

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("pause_requested", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_table(
        "run_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("action", sa.String(32), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_messages_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_messages")),
    )
    op.create_index("ix_run_messages_run_id_id", "run_messages", ["run_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_run_messages_run_id_id", table_name="run_messages")
    op.drop_table("run_messages")
    op.drop_column("runs", "pause_requested")
