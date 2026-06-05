"""Add refutation_line, refutation_line_san, refutation_eval to analysis_moves

Revision ID: 011
Revises: 010
Create Date: 2026-06-02

Adds three nullable columns to analysis_moves to persist the engine's
refutation line after each blunder:

- refutation_line      TEXT   — opponent refutation moves in UCI notation (JSON array)
- refutation_line_san  TEXT   — same line in SAN notation (JSON array)
- refutation_eval      INTEGER — evaluation after the blunder (centipawns, player POV)

These columns were previously added on-demand via ALTER TABLE inside
AnalysisRepository._ensure_refutation_columns (called on every read
and write). That approach worked but issued a write-transaction on
every request. This migration formalises the schema change and allows
the per-request guard to be removed.

Existing rows receive NULL for all three columns, which is the correct
sentinel value (no refutation data available for analyses run before
this revision).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("PRAGMA table_info(analysis_moves)"))
    existing = {row.name for row in result}

    if "refutation_line" not in existing:
        op.add_column(
            "analysis_moves",
            sa.Column("refutation_line", sa.Text(), nullable=True),
        )
    if "refutation_line_san" not in existing:
        op.add_column(
            "analysis_moves",
            sa.Column("refutation_line_san", sa.Text(), nullable=True),
        )
    if "refutation_eval" not in existing:
        op.add_column(
            "analysis_moves",
            sa.Column("refutation_eval", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("analysis_moves", "refutation_eval")
    op.drop_column("analysis_moves", "refutation_line_san")
    op.drop_column("analysis_moves", "refutation_line")
