"""github automation: watched_repos and issue_runs

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watched_repos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_s", sa.BigInteger(), nullable=False),
        sa.Column("extra_reviewers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watched_repos")),
        sa.UniqueConstraint("repo", name=op.f("uq_watched_repos_repo")),
    )
    op.create_table(
        "issue_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("repo", sa.String(200), nullable=False),
        sa.Column("issue_number", sa.BigInteger(), nullable=False),
        sa.Column("trigger", sa.String(100), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("comment_id", sa.BigInteger(), nullable=True),
        sa.Column("comment_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_issue_runs_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_runs")),
    )
    op.create_index(
        "ux_issue_runs_trigger", "issue_runs", ["repo", "issue_number", "trigger"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_issue_runs_trigger", table_name="issue_runs")
    op.drop_table("issue_runs")
    op.drop_table("watched_repos")
