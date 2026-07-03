"""Google Sheets connector — appends a row per analysis to the user's sheet.

The user provides:
  - sheets_id           : the spreadsheet ID (from the URL between /d/ and /edit)
  - sheets_credentials  : the full JSON content of a Google service account
                          key file, encrypted in the DB

The service account must have at least Editor access on the target spreadsheet
(the user shares the sheet with the service account's email, just like sharing
with any other Google user).

gspread is synchronous, so the actual call is wrapped in asyncio.to_thread to
keep the agent's async event loop responsive.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from backend.connectors.base import AnalysisPayload, BaseConnector, ConnectorResult
from backend.db.models import UserIntegration
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger
from backend.utils.security import decrypt_secret

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Column order — the connector writes in this exact order. The user's sheet
# should have a header row in the same order for readability; the connector
# itself doesn't enforce it.
_COLUMNS = [
    "timestamp",
    "analysis_id",
    "call_id",
    "user_email",
    "platform",
    "duration_secs",
    "call_type",
    "overall_score",
    "score_band",
    "alert_level",
    "next_step_quality",
    "next_step_action",
    "next_step_owner",
    "strengths",
    "improvements",
    "objections_count",
    "buying_signals_count",
    "loss_risks_count",
    "competitors_count",
    "talk_ratio_rep",
    "talk_ratio_prospect",
    "ai_summary",
]


def _row(payload: AnalysisPayload) -> list:
    joined = lambda items: " | ".join(items) if items else ""  # noqa: E731
    return [
        payload.created_at_iso or datetime.now(timezone.utc).isoformat(),
        payload.analysis_id,
        payload.call_id,
        payload.user_email,
        payload.platform,
        payload.duration_secs,
        payload.call_type,
        payload.overall_score,
        payload.score_band,
        payload.alert_level,
        payload.next_step_quality,
        payload.next_step_action,
        payload.next_step_owner,
        joined(payload.strengths),
        joined(payload.improvements),
        len(payload.objections or []),
        len(payload.buying_signals or []),
        len(payload.loss_risk_categories or []),
        len(payload.competitors_mentioned or []),
        payload.talk_ratio_rep,
        payload.talk_ratio_prospect,
        (payload.ai_summary or "")[:1900],  # google sheets cell soft limit
    ]


def _append_sync(creds_json: str, sheet_id: str, row: list) -> str:
    """Synchronous gspread call. Returns the sheet URL on success.
    Must be called via asyncio.to_thread.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
    client = gspread.authorize(creds)
    sh = client.open_by_key(sheet_id)
    worksheet = sh.sheet1  # first tab
    worksheet.append_row(row, value_input_option="USER_ENTERED")
    return sh.url


class SheetsConnector(BaseConnector):
    name = "sheets"

    async def _load_integration(self, user_id: str | uuid.UUID) -> tuple[str, str] | None:
        async with AsyncSessionLocal() as session:
            r = await session.execute(
                select(UserIntegration).where(
                    UserIntegration.user_id == uuid.UUID(str(user_id))
                )
            )
            row = r.scalar_one_or_none()
            if row is None or not row.sheets_id or not row.sheets_credentials:
                return None
            try:
                creds_json = decrypt_secret(row.sheets_credentials)
            except Exception as exc:
                logger.error("sheets: decrypt creds failed: {}", exc)
                return None
            return creds_json, row.sheets_id

    async def write_analysis(self, payload: AnalysisPayload) -> ConnectorResult:
        loaded = await self._load_integration(payload.user_id)
        if loaded is None:
            return ConnectorResult(
                connector=self.name,
                ok=False,
                error="not configured (sheets_id / sheets_credentials missing)",
            )
        creds_json, sheet_id = loaded
        try:
            url = await asyncio.to_thread(_append_sync, creds_json, sheet_id, _row(payload))
            logger.info("sheets: appended row for analysis {}", payload.analysis_id)
            return ConnectorResult(
                connector=self.name, ok=True, detail="row appended", external_url=url
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("sheets connector failed: {}", exc)
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))

    async def test_connection(self, user_id: str | uuid.UUID) -> ConnectorResult:
        loaded = await self._load_integration(user_id)
        if loaded is None:
            return ConnectorResult(connector=self.name, ok=False, error="not configured")
        creds_json, sheet_id = loaded
        try:
            def _check() -> str:
                import gspread
                from google.oauth2.service_account import Credentials
                info = json.loads(creds_json)
                creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
                client = gspread.authorize(creds)
                sh = client.open_by_key(sheet_id)
                return f"{sh.title} ({sh.sheet1.title})"
            title = await asyncio.to_thread(_check)
            return ConnectorResult(
                connector=self.name, ok=True, detail=f"connected: {title}"
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))
