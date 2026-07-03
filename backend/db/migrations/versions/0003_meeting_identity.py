"""add call_title + prospect_name to analyses

Two lightweight columns so alert emails can show a proper meeting header
(who the call was with + a concrete title) instead of a generic subject.

Revision ID: 0003_meeting_identity
Revises: 0002_curated_analysis_fields
Create Date: 2026-06-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_meeting_identity"
down_revision: Union[str, None] = "0002_curated_analysis_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("call_title", sa.String(255), nullable=True))
    op.add_column("analyses", sa.Column("prospect_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("analyses", "prospect_name")
    op.drop_column("analyses", "call_title")
