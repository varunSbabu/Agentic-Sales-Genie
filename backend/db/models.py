"""SQLAlchemy 2.0 declarative models for Sales Genie.

All identifiers use server-side `gen_random_uuid()` so the database is the source
of truth for primary keys (requires the `pgcrypto` extension — the initial
migration enables it).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    alert_threshold_low: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    alert_threshold_high: Mapped[float] = mapped_column(Float, nullable=False, default=4.0)
    notify_email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_slack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manager_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    kb_documents: Mapped[list[KBDocument]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    calls: Mapped[list[Call]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    integration: Mapped[UserIntegration | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


# ---------------------------------------------------------------------------
# kb_documents
# ---------------------------------------------------------------------------
class KBDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pending | processing | ready | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="kb_documents")


# ---------------------------------------------------------------------------
# calls
# ---------------------------------------------------------------------------
class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_secs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recording_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    transcript_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_speakers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    talk_ratio_rep: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    talk_ratio_prospect: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    speaker_count: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    user: Mapped[User] = relationship(back_populates="calls")
    analysis: Mapped[Analysis | None] = relationship(
        back_populates="call", cascade="all, delete-orphan", uselist=False
    )


# ---------------------------------------------------------------------------
# analyses
# ---------------------------------------------------------------------------
class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("call_id", name="uq_analyses_call_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Meeting identity (used by alert emails + list views) ---------------
    call_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prospect_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Core classification + scoring (existing) ---------------------------
    call_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_type_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    methodology_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_band: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimension_scores: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    improvements: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Signals (new) ------------------------------------------------------
    # objections: [{ quote: "...", category: "...", was_addressed: bool, how: "..." }]
    objections: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # buying_signals: [{ quote, category, strength: "weak|medium|strong" }]
    buying_signals: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # competitors_mentioned: [{ name, context_quote, sentiment: "negative|neutral|positive" }]
    competitors_mentioned: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Loss risk (existing array kept) ------------------------------------
    loss_risk_categories: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Next step (existing label + new structured fields) -----------------
    next_step_quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_step_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_step_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Narrative output (existing + new structured) -----------------------
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # call_summary_bullets: ["bullet 1", "bullet 2", ...]
    call_summary_bullets: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # key_quotes: [{ quote, speaker, why_notable }]
    key_quotes: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # --- Action tracking ----------------------------------------------------
    # none | coaching | intervention
    alert_level: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    crm_written: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notification_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    call: Mapped[Call] = relationship(back_populates="analysis")
    user: Mapped[User] = relationship(back_populates="analyses")
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# notifications
# ---------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # email | slack
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient: Mapped[str] = mapped_column(String(512), nullable=False)
    # pending | sent | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis: Mapped[Analysis] = relationship(back_populates="notifications")
    user: Mapped[User] = relationship(back_populates="notifications")


# ---------------------------------------------------------------------------
# user_integrations
# ---------------------------------------------------------------------------
class UserIntegration(Base):
    __tablename__ = "user_integrations"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_integrations_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    notion_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    notion_database_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheets_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheets_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    slack_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    slack_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_manager_dm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="integration")
