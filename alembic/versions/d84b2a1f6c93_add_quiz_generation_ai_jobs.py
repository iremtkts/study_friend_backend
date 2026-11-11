"""add quiz generation AI jobs

Revision ID: d84b2a1f6c93
Revises: c73d91e5a804
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d84b2a1f6c93"
down_revision: Union[str, None] = "c73d91e5a804"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_ai_jobs_job_type", "ai_jobs", type_="check")
    op.create_check_constraint(
        "ck_ai_jobs_job_type",
        "ai_jobs",
        "job_type IN ('summarize_note', 'index_note', 'chat_message', 'generate_quiz')",
    )
    op.add_column("ai_jobs", sa.Column("result_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_jobs", "result_id")
    op.drop_constraint("ck_ai_jobs_job_type", "ai_jobs", type_="check")
    op.create_check_constraint(
        "ck_ai_jobs_job_type",
        "ai_jobs",
        "job_type IN ('summarize_note', 'index_note', 'chat_message')",
    )
