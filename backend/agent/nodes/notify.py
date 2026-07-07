"""Notification dispatch node — Phase 7 wiring.

Sends actual email (SendGrid) + Slack (if configured) when alert_level != none.
Writes a row into the `notifications` table per delivery attempt so we have
an audit trail for who got pinged when.

Recipient logic:
  - If manager_email is set on the user, notifications go there
  - Otherwise, notifications go to the user's own email (useful for testing)
  - notify_email=False on the user disables email dispatch entirely
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from backend.agent.state import GenieState
from backend.db.models import Notification, User
from backend.db.session import AsyncSessionLocal
from backend.notifications.email import (
    AlertEmailPayload,
    EmailDeliveryError,
    send_alert_email,
)
from backend.notifications.slack import SlackAlertPayload, send_slack_alert
from backend.utils.logging import logger


async def _write_notification_row(
    session,
    *,
    analysis_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    recipient: str,
    status_: str,
) -> None:
    row = Notification(
        analysis_id=analysis_id,
        user_id=user_id,
        channel=channel,
        recipient=recipient,
        status=status_,
        sent_at=datetime.now(timezone.utc) if status_ == "sent" else None,
    )
    session.add(row)


async def send_notification_node(state: GenieState) -> dict:
    # An overview email is sent for every analyzed call; the alert level only
    # changes the banner + recommendation. No early-return for "none".
    level = state.get("alert_level", "none")

    analysis_id = state.get("analysis_id")
    if not analysis_id:
        logger.warning("notify: no analysis_id — skipping (upstream write_db likely failed)")
        return {"notification_sent": False}

    # Load the user's contact + preferences
    async with AsyncSessionLocal() as session:
        r = await session.execute(
            select(User).where(User.id == uuid.UUID(str(state["user_id"])))
        )
        user = r.scalar_one_or_none()
        if user is None:
            return {"notification_sent": False}

        recipient_email = user.manager_email or user.email
        rep_name = user.full_name or "Sales Rep"

        email_ok = False
        slack_ok = False

        # --- Email ---------------------------------------------------------
        if user.notify_email and recipient_email:
            payload = AlertEmailPayload(
                recipient_email=recipient_email,
                rep_name=rep_name,
                rep_email=user.email,
                # Meeting identity (used for the header line + subject)
                call_title=state.get("call_title") or "",
                prospect_name=state.get("prospect_name") or "",
                call_type=state.get("call_type") or "—",
                duration_secs=int(state.get("duration_secs") or 0),
                # Score
                overall_score=float(state.get("overall_score") or 0.0),
                score_band=state.get("score_band") or "",
                score_justification=state.get("score_justification") or "",
                next_step_quality=state.get("next_step_quality") or "",
                # Narrative
                ai_summary=state.get("ai_summary") or "",
                call_notes=state.get("call_notes") or "",
                call_summary_bullets=list(state.get("call_summary_bullets") or []),
                # Full detail for the professional overview
                dimension_scores=list(state.get("dimension_scores") or []),
                dimension_scores_count=len(state.get("dimension_scores") or []),
                key_quotes=list(state.get("key_quotes") or []),
                # Reasoning inputs
                strengths=list(state.get("strengths") or []),
                improvements=list(state.get("improvements") or []),
                loss_risk_categories=list(state.get("loss_risk_categories") or []),
                objections=list(state.get("objections") or []),
                buying_signals=list(state.get("buying_signals") or []),
                competitors_mentioned=list(state.get("competitors_mentioned") or []),
                # Next step
                next_step_action=state.get("next_step_action") or "",
                next_step_owner=state.get("next_step_owner") or "",
                analysis_url=f"http://localhost:8000/#analysis-{analysis_id}",
            )
            try:
                await send_alert_email(payload, alert_level=level)
                email_ok = True
                await _write_notification_row(
                    session, analysis_id=uuid.UUID(analysis_id), user_id=user.id,
                    channel="email", recipient=recipient_email, status_="sent",
                )
                logger.info("notify: email → {} ok", recipient_email)
            except EmailDeliveryError as exc:
                await _write_notification_row(
                    session, analysis_id=uuid.UUID(analysis_id), user_id=user.id,
                    channel="email", recipient=recipient_email, status_="failed",
                )
                logger.warning("notify: email failed: {}", exc)
        elif not user.notify_email:
            logger.info("notify: email disabled by user preference")
        elif not recipient_email:
            logger.info("notify: no manager_email and no user email — skipping email")

        # --- Slack ---------------------------------------------------------
        if user.notify_slack:
            slack_payload = SlackAlertPayload(
                channel="",  # populated from user_integrations.slack_channel
                rep_name=rep_name,
                call_type=state.get("call_type") or "—",
                overall_score=float(state.get("overall_score") or 0.0),
                score_band=state.get("score_band") or "",
                strengths=list(state.get("strengths") or []),
                improvements=list(state.get("improvements") or []),
                loss_risk_categories=list(state.get("loss_risk_categories") or []),
                analysis_url=f"http://localhost:8000/#analysis-{analysis_id}",
            )
            slack_result = await send_slack_alert(user.id, slack_payload, alert_level=level)
            if slack_result and slack_result.get("ok"):
                slack_ok = True
                await _write_notification_row(
                    session, analysis_id=uuid.UUID(analysis_id), user_id=user.id,
                    channel="slack", recipient="(configured channel)", status_="sent",
                )
            elif slack_result is None:
                logger.info("notify: slack enabled but no token — skipping")
            else:
                await _write_notification_row(
                    session, analysis_id=uuid.UUID(analysis_id), user_id=user.id,
                    channel="slack", recipient="(configured channel)", status_="failed",
                )

        await session.commit()

    return {"notification_sent": email_ok or slack_ok}


async def skip_notification_node(state: GenieState) -> dict:
    return {"notification_sent": False}
