"""curated extra analysis fields — high-signal subset of Workato schema

Adds 10 columns to `analyses` that earn their place: justifications,
structured objections/buying_signals/competitors, call-summary bullets,
key_quotes, next-step structure, and methodology audit trail.

Skipped (intentionally): redundant counts (derivable from JSONB arrays
via jsonb_array_length), Workato-proprietary fields (Leverage/Validate
stage names, PROPOSAL_PASSTHROUGH aggregation rule, secondary lens
scores) — none of those apply to our framework.

Revision ID: 0002_curated_analysis_fields
Revises: 0001_initial
Create Date: 2026-06-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_curated_analysis_fields"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("call_type_justification", sa.Text(), nullable=True))
    op.add_column("analyses", sa.Column("methodology_id", sa.String(64), nullable=True))
    op.add_column("analyses", sa.Column("score_justification", sa.Text(), nullable=True))
    op.add_column("analyses", sa.Column("objections", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("analyses", sa.Column("buying_signals", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("analyses", sa.Column("competitors_mentioned", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("analyses", sa.Column("next_step_action", sa.Text(), nullable=True))
    op.add_column("analyses", sa.Column("next_step_owner", sa.String(255), nullable=True))
    op.add_column("analyses", sa.Column("call_summary_bullets", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("analyses", sa.Column("key_quotes", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("analyses", "key_quotes")
    op.drop_column("analyses", "call_summary_bullets")
    op.drop_column("analyses", "next_step_owner")
    op.drop_column("analyses", "next_step_action")
    op.drop_column("analyses", "competitors_mentioned")
    op.drop_column("analyses", "buying_signals")
    op.drop_column("analyses", "objections")
    op.drop_column("analyses", "score_justification")
    op.drop_column("analyses", "methodology_id")
    op.drop_column("analyses", "call_type_justification")
