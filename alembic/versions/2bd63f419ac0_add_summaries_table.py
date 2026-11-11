"""add summaries table

Revision ID: 2bd63f419ac0
Revises: f7c3a91e2b64
Create Date: 2026-11-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2bd63f419ac0"
down_revision: Union[str, None] = "f7c3a91e2b64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('ai', 'manual')", name="ck_summaries_source"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_summaries_note_id", "summaries", ["note_id"], unique=False)
    op.create_index("ix_summaries_user_id", "summaries", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_summaries_user_id", table_name="summaries")
    op.drop_index("ix_summaries_note_id", table_name="summaries")
    op.drop_table("summaries")
