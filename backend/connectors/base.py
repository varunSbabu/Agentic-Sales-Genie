"""Abstract connector contract — every destination implements this.

The factory fans out a single AnalysisPayload to every connector configured
for the user via asyncio.gather, so each connector must be async-friendly
even if its underlying SDK is synchronous (wrap in asyncio.to_thread).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AnalysisPayload:
    """The full set of analysis fields a connector might want to render.

    Built once in the factory and handed to every connector — same input
    shape regardless of destination. Connectors decide which fields to render.
    """

    analysis_id: str
    call_id: str
    user_id: str
    user_email: str

    call_type: str
    call_type_justification: str
    methodology_id: str
    overall_score: float
    score_band: str
    score_justification: str
    dimension_scores: list[dict]
    strengths: list[str]
    improvements: list[str]
    objections: list[dict]
    buying_signals: list[dict]
    competitors_mentioned: list[dict]
    next_step_quality: str
    next_step_action: str
    next_step_owner: str
    loss_risk_categories: list[str]
    ai_summary: str
    call_notes: str
    call_summary_bullets: list[str]
    key_quotes: list  # str now; dict for legacy rows
    alert_level: str

    platform: str = "manual"
    duration_secs: int = 0
    talk_ratio_rep: float = 0.0
    talk_ratio_prospect: float = 0.0
    question_count: int = 0
    created_at_iso: str = ""

    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorResult:
    """What a connector returns to the factory."""

    connector: str
    ok: bool
    detail: str = ""
    external_url: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "connector": self.connector,
            "ok": self.ok,
            "detail": self.detail,
            "external_url": self.external_url,
            "error": self.error,
        }


class BaseConnector(ABC):
    """Every output destination implements this two-method interface."""

    name: str = "base"

    @abstractmethod
    async def write_analysis(self, payload: AnalysisPayload) -> ConnectorResult:
        """Write the analysis to the destination. Should NEVER raise; convert
        all errors into ConnectorResult(ok=False, error=...).

        Returns the result with optional external_url so the UI can deep-link.
        """

    @abstractmethod
    async def test_connection(self, user_id: str | uuid.UUID) -> ConnectorResult:
        """Verify credentials. Used by the test endpoints in /notifications/test."""
