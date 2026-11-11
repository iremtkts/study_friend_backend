"""add ai jobs table

Revision ID: 81e4b3d9a62f
Revises: 6c7a9d2f1e43
Create Date: 2026-11-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "81e4b3d9a62f"
down_revision: Union[str, None] = "6c7a9d2f1e43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "ai_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("result_count", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("job_type IN ('summarize_note')", name="ck_ai_jobs_job_type"),
        sa.CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name="ck_ai_jobs_status"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_jobs_user_created_at", "ai_jobs", ["user_id", "created_at"], unique=False)
    op.create_index(
        "uq_ai_jobs_one_active_per_note",
        "ai_jobs",
        ["user_id", "job_type", "note_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_ai_jobs_one_active_per_note", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_user_created_at", table_name="ai_jobs")
    op.drop_table("ai_jobs")
