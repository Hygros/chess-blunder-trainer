"""Add llm_explanation_version column to analysis_moves

Revision ID: 013
Revises: 012
Create Date: 2026-06-04

Adds a nullable INTEGER column to store the prompt/output schema version
for llm_explanation. This allows controlled refreshes when the coaching
format changes in a non-backward-compatible way.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("PRAGMA table_info(analysis_moves)"))
    existing = {row.name for row in result}

    if "llm_explanation_version" not in existing:
        op.add_column(
            "analysis_moves",
            sa.Column("llm_explanation_version", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("analysis_moves", "llm_explanation_version")
