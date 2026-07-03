"""Celery task: (re)send notifications for an existing analysis.

Notifications normally fire inline inside the LangGraph `notify` node during
analysis. This task exists for RETRIES / MANUAL RE-SEND — e.g. SendGrid was
down when the analysis ran, or a manager was added later and needs the alert.

It loads a persisted Analysis row and re-runs the email/Slack dispatch.
"""

from __future__ import annotations

import asyncio
import uuid

from backend.tasks.celery_app import celery_app
from backend.utils.logging import logger


async def _resend(analysis_id: str) -> dict:
    from sqlalchemy import select

    from backend.db.models import Analysis, User
    from backend.db.session import AsyncSessionLocal
    from backend.notifications.email import AlertEmailPayload, send_alert_email

    async with AsyncSessionLocal() as session:
        a = (
            await session.execute(
                select(Analysis).where(Analysis.id == uuid.UUID(analysis_id))
            )
        ).scalar_one_or_none()
        if a is None:
            return {"ok": False, "error": f"analysis {analysis_id} not found"}
        if a.alert_level == "none":
            return {"ok": False, "error": "analysis has no alert to send"}

        user = (
            await session.execute(select(User).where(User.id == a.user_id))
        ).scalar_one_or_none()
        if user is None:
            return {"ok": False, "error": "user not found"}

        recipient = user.manager_email or user.email
        if not (user.notify_email and recipient):
            return {"ok": False, "error": "email notifications disabled or no recipient"}

    payload = AlertEmailPayload(
        recipient_email=recipient,
        rep_name=user.full_name or "Sales Rep",
        rep_email=user.email,
        call_title=a.call_title or "",
        prospect_name=a.prospect_name or "",
        call_type=a.call_type or "—",
        overall_score=float(a.overall_score or 0.0),
        score_band=a.score_band or "",
        score_justification=a.score_justification or "",
        next_step_quality=a.next_step_quality or "",
        ai_summary=a.ai_summary or "",
        dimension_scores_count=len(a.dimension_scores or []),
        strengths=list(a.strengths or []),
        improvements=list(a.improvements or []),
        loss_risk_categories=list(a.loss_risk_categories or []),
        objections=list(a.objections or []),
        buying_signals=list(a.buying_signals or []),
        next_step_action=a.next_step_action or "",
        next_step_owner=a.next_step_owner or "",
        analysis_url=f"http://localhost:8000/#analysis-{analysis_id}",
    )
    try:
        await send_alert_email(payload, alert_level=a.alert_level)
        return {"ok": True, "recipient": recipient, "alert_level": a.alert_level}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


@celery_app.task(name="notifications.resend", bind=True)
def send_notifications_task(self, analysis_id: str) -> dict:
    logger.info("send_notifications_task job={} analysis={}", self.request.id, analysis_id)
    return asyncio.run(_resend(analysis_id))
