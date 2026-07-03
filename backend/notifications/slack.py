"""Slack Bolt notifications — stub for future wiring.

Full Block Kit templates for INTERVENTION + COACHING alerts are defined here
so the send_notification_node can call them once the user's Slack token is
configured. Actual delivery is a no-op if no token exists — safe to call
regardless of setup state.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from backend.db.models import UserIntegration
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger
from backend.utils.security import decrypt_secret


@dataclass
class SlackAlertPayload:
    channel: str
    rep_name: str
    call_type: str
    overall_score: float
    score_band: str
    strengths: list[str]
    improvements: list[str]
    loss_risk_categories: list[str]
    analysis_url: str = ""


def build_intervention_blocks(p: SlackAlertPayload) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "🚨 Intervention Required"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Rep:* {p.rep_name}  |  *Score:* {p.overall_score:.1f}/5.0  |  *{p.score_band}*"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Call Type:* {p.call_type}"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "*Top risks:*\n" + "\n".join(f"• {r}" for r in p.loss_risk_categories[:3]) if p.loss_risk_categories else "*Top risks:* none identified"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "*Immediate actions required:*\n" + "\n".join(f"• {i}" for i in p.improvements[:3])}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "View Full Analysis"},
             "url": p.analysis_url or "http://localhost:8000/", "style": "danger"}
        ]},
    ]


def build_coaching_blocks(p: SlackAlertPayload) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "⭐ Coaching Example"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Rep:* {p.rep_name}  |  *Score:* {p.overall_score:.1f}/5.0  |  *{p.score_band}*"}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "*What went well:*\n" + "\n".join(f"• {s}" for s in p.strengths[:3])}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "View Analysis"},
             "url": p.analysis_url or "http://localhost:8000/", "style": "primary"}
        ]},
    ]


async def _load_slack_token(user_id: str | uuid.UUID) -> tuple[str, str] | None:
    async with AsyncSessionLocal() as session:
        r = await session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == uuid.UUID(str(user_id))
            )
        )
        row = r.scalar_one_or_none()
        if row is None or not row.slack_token:
            return None
        try:
            token = decrypt_secret(row.slack_token)
        except Exception as exc:  # noqa: BLE001
            logger.error("slack: decrypt token failed: {}", exc)
            return None
        channel = row.slack_channel or "#sales-genie"
        return token, channel


async def send_slack_alert(
    user_id: str | uuid.UUID,
    payload: SlackAlertPayload,
    *,
    alert_level: str,
) -> dict | None:
    """Send Slack alert if the user has a token configured. Returns None if unconfigured."""
    creds = await _load_slack_token(user_id)
    if creds is None:
        logger.info("slack: no token configured for user {} — skipping", user_id)
        return None
    token, channel = creds
    blocks = (
        build_intervention_blocks(payload)
        if alert_level == "intervention"
        else build_coaching_blocks(payload)
    )
    try:
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=token)
        resp = await client.chat_postMessage(
            channel=payload.channel or channel,
            blocks=blocks,
            text=f"Sales Genie alert: {alert_level}",  # fallback for notifications
        )
        return {"ok": bool(resp.get("ok")), "ts": resp.get("ts")}
    except Exception as exc:  # noqa: BLE001
        logger.exception("slack send failed: {}", exc)
        return {"ok": False, "error": str(exc)}
