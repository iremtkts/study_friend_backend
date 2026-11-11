"""add chat jobs and message sources

Revision ID: c73d91e5a804
Revises: a42c9f61d782
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c73d91e5a804"
down_revision: Union[str, None] = "a42c9f61d782"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_ai_jobs_job_type", "ai_jobs", type_="check")
    op.create_check_constraint(
        "ck_ai_jobs_job_type",
        "ai_jobs",
        "job_type IN ('summarize_note', 'index_note', 'chat_message')",
    )
    op.add_column("ai_jobs", sa.Column("conversation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_ai_jobs_conversation_id_conversations",
        "ai_jobs",
        "conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_ai_jobs_one_active_per_conversation",
        "ai_jobs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("job_type = 'chat_message' AND status IN ('pending', 'running')"),
    )
    op.add_column(
        "messages",
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "sources")
    op.drop_index("uq_ai_jobs_one_active_per_conversation", table_name="ai_jobs")
    op.drop_constraint("fk_ai_jobs_conversation_id_conversations", "ai_jobs", type_="foreignkey")
    op.drop_column("ai_jobs", "conversation_id")
    op.drop_constraint("ck_ai_jobs_job_type", "ai_jobs", type_="check")
    op.create_check_constraint(
        "ck_ai_jobs_job_type",
        "ai_jobs",
        "job_type IN ('summarize_note', 'index_note')",
    )
