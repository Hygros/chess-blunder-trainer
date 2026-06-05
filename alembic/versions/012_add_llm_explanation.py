"""Add llm_explanation column to analysis_moves

Revision ID: 012
Revises: 011
Create Date: 2026-06-02

Adds a nullable TEXT column to analysis_moves for storing pre-computed
LLM training explanations. When populated (via the backfill_llm job or
the llm pipeline step), the web API uses the stored value directly so
no live LLM call is needed at training time.

Existing rows receive NULL (no explanation yet). The backfill_llm job
fills these in on demand.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_moves",
        sa.Column("llm_explanation", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_moves", "llm_explanation")
