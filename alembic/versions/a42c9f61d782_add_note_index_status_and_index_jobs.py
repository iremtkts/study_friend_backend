"""add note indexing state and index_note jobs

Revision ID: a42c9f61d782
Revises: 81e4b3d9a62f
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a42c9f61d782"
down_revision: Union[str, None] = "81e4b3d9a62f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("index_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    )
    op.add_column("notes", sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notes", sa.Column("chunk_count", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_notes_index_status", "notes", "index_status IN ('pending', 'indexed', 'failed')"
    )
    op.drop_constraint("ck_ai_jobs_job_type", "ai_jobs", type_="check")
    op.create_check_constraint(
        "ck_ai_jobs_job_type", "ai_jobs", "job_type IN ('summarize_note', 'index_note')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_ai_jobs_job_type", "ai_jobs", type_="check")
    op.create_check_constraint("ck_ai_jobs_job_type", "ai_jobs", "job_type IN ('summarize_note')")
    op.drop_constraint("ck_notes_index_status", "notes", type_="check")
    op.drop_column("notes", "chunk_count")
    op.drop_column("notes", "indexed_at")
    op.drop_column("notes", "index_status")
