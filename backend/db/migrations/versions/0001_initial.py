"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("alert_threshold_low", sa.Float(), nullable=False, server_default="2.5"),
        sa.Column("alert_threshold_high", sa.Float(), nullable=False, server_default="4.0"),
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_slack", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("manager_email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "kb_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("file_type", sa.String(32), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("collection_name", sa.String(255), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_kb_documents_user_id", "kb_documents", ["user_id"])

    op.create_table(
        "calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(64), nullable=False),
        sa.Column("duration_secs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recording_url", sa.String(1024), nullable=True),
        sa.Column("transcript_raw", sa.Text(), nullable=True),
        sa.Column("transcript_speakers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("talk_ratio_rep", sa.Float(), nullable=False, server_default="0"),
        sa.Column("talk_ratio_prospect", sa.Float(), nullable=False, server_default="0"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("speaker_count", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_calls_user_id", "calls", ["user_id"])
    op.create_index("ix_calls_created_at", "calls", ["created_at"])

    op.create_table(
        "analyses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_type", sa.String(64), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("score_band", sa.String(32), nullable=True),
        sa.Column("dimension_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("strengths", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("improvements", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_step_quality", sa.String(64), nullable=True),
        sa.Column("loss_risk_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("call_notes", sa.Text(), nullable=True),
        sa.Column("alert_level", sa.String(32), nullable=False, server_default="none"),
        sa.Column("crm_written", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notification_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("call_id", name="uq_analyses_call_id"),
    )
    op.create_index("ix_analyses_user_id", "analyses", ["user_id"])
    op.create_index("ix_analyses_call_id", "analyses", ["call_id"])
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])

    op.create_table(
        "notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "analysis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("recipient", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notifications_analysis_id", "notifications", ["analysis_id"])
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])

    op.create_table(
        "user_integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notion_token", sa.Text(), nullable=True),
        sa.Column("notion_database_id", sa.String(255), nullable=True),
        sa.Column("sheets_id", sa.String(255), nullable=True),
        sa.Column("sheets_credentials", sa.Text(), nullable=True),
        sa.Column("slack_token", sa.Text(), nullable=True),
        sa.Column("slack_channel", sa.String(255), nullable=True),
        sa.Column("slack_manager_dm", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_user_integrations_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_integrations")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_index("ix_notifications_analysis_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_index("ix_analyses_call_id", table_name="analyses")
    op.drop_index("ix_analyses_user_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_calls_created_at", table_name="calls")
    op.drop_index("ix_calls_user_id", table_name="calls")
    op.drop_table("calls")
    op.drop_index("ix_kb_documents_user_id", table_name="kb_documents")
    op.drop_table("kb_documents")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
