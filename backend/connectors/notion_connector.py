"""Notion connector — creates a page in the user's Notion database.

The user pre-creates a Notion database with these properties (we don't
auto-create them — Notion's schema API is rigid). The connector tolerates
missing properties (just skips them) but works best when the database has:

  - Title              : title          (mapped from call_type + score)
  - Score              : number
  - Band               : select         (EXCELLENT/SOLID/MIXED/INTERVENTION REQUIRED)
  - Call Type          : select         (Discovery/Demo/Commercial/Service/...)
  - Alert              : select         (intervention/coaching/none)
  - Next Step          : rich_text
  - Strengths          : rich_text
  - Improvements       : rich_text
  - Buying Signals     : number         (count)
  - Objections         : number         (count)
  - Loss Risk Count    : number
  - Created            : date
  - AI Summary         : rich_text

We write all properties we know about; Notion ignores ones the database
doesn't define. The page body also gets the AI summary as a paragraph block.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from backend.connectors.base import AnalysisPayload, BaseConnector, ConnectorResult
from backend.db.models import UserIntegration
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger
from backend.utils.security import decrypt_secret


def _title(payload: AnalysisPayload) -> str:
    return f"{payload.call_type or 'Call'} — {payload.score_band or '—'} ({payload.overall_score:.1f}/5)"


def _truncate(text: str, n: int = 1900) -> str:
    """Notion rich_text segments cap at 2000 chars."""
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _properties(payload: AnalysisPayload) -> dict:
    """Best-effort property map. Notion ignores keys the database doesn't define,
    so passing extras is safe."""
    return {
        "Title": {"title": [{"text": {"content": _title(payload)}}]},
        "Name": {"title": [{"text": {"content": _title(payload)}}]},
        "Score": {"number": float(payload.overall_score)},
        "Band": {"select": {"name": payload.score_band or "—"}},
        "Call Type": {"select": {"name": payload.call_type or "Other"}},
        "Alert": {"select": {"name": payload.alert_level or "none"}},
        "Next Step": {
            "rich_text": [{"text": {"content": _truncate(payload.next_step_action or "—")}}]
        },
        "Strengths": {
            "rich_text": [
                {"text": {"content": _truncate("\n".join(f"• {s}" for s in (payload.strengths or [])))}}
            ]
        },
        "Improvements": {
            "rich_text": [
                {"text": {"content": _truncate("\n".join(f"• {s}" for s in (payload.improvements or [])))}}
            ]
        },
        "Buying Signals": {"number": len(payload.buying_signals or [])},
        "Objections": {"number": len(payload.objections or [])},
        "Loss Risk Count": {"number": len(payload.loss_risk_categories or [])},
        "Created": {"date": {"start": payload.created_at_iso or datetime.now(timezone.utc).isoformat()}},
        "AI Summary": {
            "rich_text": [{"text": {"content": _truncate(payload.ai_summary or "")}}]
        },
    }


def _body_blocks(payload: AnalysisPayload) -> list[dict]:
    """Notion page body — paragraphs for the AI summary + call notes + key quotes."""
    blocks: list[dict] = []

    def heading(text: str) -> dict:
        return {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": text}}]},
        }

    def para(text: str) -> dict:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": _truncate(text)}}]},
        }

    if payload.ai_summary:
        blocks += [heading("AI Summary"), para(payload.ai_summary)]
    if payload.call_notes:
        blocks += [heading("Call Notes"), para(payload.call_notes)]
    if payload.next_step_action:
        blocks += [
            heading("Next Step"),
            para(f"{payload.next_step_action} (owner: {payload.next_step_owner or '—'})"),
        ]
    if payload.score_justification:
        blocks += [heading("Score Justification"), para(payload.score_justification)]
    return blocks


class NotionConnector(BaseConnector):
    name = "notion"

    async def _load_integration(self, user_id: str | uuid.UUID) -> tuple[str, str] | None:
        async with AsyncSessionLocal() as session:
            r = await session.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == uuid.UUID(str(user_id))
                )
            )
            row = r.scalar_one_or_none()
            if row is None or not row.notion_token or not row.notion_database_id:
                return None
            try:
                token = decrypt_secret(row.notion_token)
            except Exception as exc:
                logger.error("notion: decrypt token failed: {}", exc)
                return None
            return token, row.notion_database_id

    async def write_analysis(self, payload: AnalysisPayload) -> ConnectorResult:
        creds = await self._load_integration(payload.user_id)
        if creds is None:
            return ConnectorResult(
                connector=self.name,
                ok=False,
                error="not configured (notion_token / notion_database_id missing)",
            )
        token, database_id = creds
        try:
            from notion_client import AsyncClient
            client = AsyncClient(auth=token)
            page = await client.pages.create(
                parent={"database_id": database_id},
                properties=_properties(payload),
                children=_body_blocks(payload),
            )
            page_id = page.get("id", "")
            url = page.get("url") or (
                f"https://notion.so/{page_id.replace('-', '')}" if page_id else None
            )
            await client.aclose()
            logger.info("notion: created page {}", page_id)
            return ConnectorResult(
                connector=self.name, ok=True, detail=f"page {page_id}", external_url=url
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("notion connector failed: {}", exc)
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))

    async def test_connection(self, user_id: str | uuid.UUID) -> ConnectorResult:
        creds = await self._load_integration(user_id)
        if creds is None:
            return ConnectorResult(
                connector=self.name, ok=False, error="not configured"
            )
        token, database_id = creds
        try:
            from notion_client import AsyncClient
            client = AsyncClient(auth=token)
            db = await client.databases.retrieve(database_id=database_id)
            await client.aclose()
            title_parts = db.get("title") or []
            title = " ".join(t.get("plain_text", "") for t in title_parts) or "(untitled)"
            return ConnectorResult(
                connector=self.name,
                ok=True,
                detail=f"connected to database: {title}",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))
