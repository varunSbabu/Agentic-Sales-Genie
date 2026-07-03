"""Factory + fan-out for connectors.

`dispatch_to_all` runs every configured connector in parallel via asyncio.gather.
A failing connector returns ConnectorResult(ok=False) — it does NOT raise — so
one outage doesn't take the rest down.

Supabase is always included (the canonical store). Notion / Sheets are only
included if the user has configured them.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from backend.connectors.base import AnalysisPayload, BaseConnector, ConnectorResult
from backend.connectors.notion_connector import NotionConnector
from backend.connectors.sheets_connector import SheetsConnector
from backend.connectors.supabase_connector import SupabaseConnector
from backend.db.models import UserIntegration
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger


async def get_connectors(user_id: str | uuid.UUID) -> list[BaseConnector]:
    """Return all connectors configured for this user. Supabase always.

    Notion / Sheets included iff the user_integrations row carries the required
    fields. Callers can rely on the returned list to determine what's wired.
    """
    connectors: list[BaseConnector] = [SupabaseConnector()]

    async with AsyncSessionLocal() as session:
        r = await session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == uuid.UUID(str(user_id))
            )
        )
        integ = r.scalar_one_or_none()

    if integ is None:
        return connectors

    if integ.notion_token and integ.notion_database_id:
        connectors.append(NotionConnector())
    if integ.sheets_id and integ.sheets_credentials:
        connectors.append(SheetsConnector())

    return connectors


async def list_configured_connectors(user_id: str | uuid.UUID) -> list[str]:
    """Lightweight version used by /config/integrations to show status."""
    cs = await get_connectors(user_id)
    return [c.name for c in cs]


async def dispatch_to_all(payload: AnalysisPayload) -> list[ConnectorResult]:
    """Fan out the payload to every configured connector in parallel."""
    connectors = await get_connectors(payload.user_id)
    logger.info(
        "dispatch: user={} analysis={} connectors={}",
        payload.user_id,
        payload.analysis_id,
        [c.name for c in connectors],
    )
    raw = await asyncio.gather(
        *[c.write_analysis(payload) for c in connectors],
        return_exceptions=True,
    )
    results: list[ConnectorResult] = []
    for c, r in zip(connectors, raw):
        if isinstance(r, Exception):
            logger.error("connector {} raised: {}", c.name, r)
            results.append(ConnectorResult(connector=c.name, ok=False, error=str(r)))
        else:
            results.append(r)
    return results
