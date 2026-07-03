"""Supabase connector — wraps the existing write_db logic in the connector interface.

In Phase 5 we wrote the Analysis row directly inside `write_db_node`. That node
still does the canonical write because we want the row in place BEFORE other
connectors fire (so they can reference analysis_id). This connector therefore
mostly returns a result pointing at the existing row + Supabase dashboard URL
— it doesn't re-write the row.
"""

from __future__ import annotations

import uuid

from backend.config import settings
from backend.connectors.base import AnalysisPayload, BaseConnector, ConnectorResult
from backend.db.models import Analysis
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger


class SupabaseConnector(BaseConnector):
    name = "supabase"

    def _dashboard_url(self, analysis_id: str) -> str | None:
        ref = settings.supabase_project_ref
        if not ref:
            return None
        return (
            f"https://supabase.com/dashboard/project/{ref}/editor?table=analyses"
            f"&filter=id:{analysis_id}"
        )

    async def write_analysis(self, payload: AnalysisPayload) -> ConnectorResult:
        """The row has already been written by write_db_node. We just confirm
        it landed and return the dashboard deep link."""
        try:
            async with AsyncSessionLocal() as session:
                row = await session.get(Analysis, uuid.UUID(payload.analysis_id))
                if row is None:
                    return ConnectorResult(
                        connector=self.name,
                        ok=False,
                        error=f"analysis {payload.analysis_id} not found in Supabase",
                    )
            return ConnectorResult(
                connector=self.name,
                ok=True,
                detail=f"row {payload.analysis_id} confirmed in analyses table",
                external_url=self._dashboard_url(payload.analysis_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("supabase connector verify failed: {}", exc)
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))

    async def test_connection(self, user_id: str | uuid.UUID) -> ConnectorResult:
        try:
            async with AsyncSessionLocal() as session:
                # cheap healthcheck — count user's analyses
                from sqlalchemy import select, func
                r = await session.execute(
                    select(func.count(Analysis.id)).where(
                        Analysis.user_id == uuid.UUID(str(user_id))
                    )
                )
                count = r.scalar() or 0
            return ConnectorResult(
                connector=self.name,
                ok=True,
                detail=f"connected — {count} analyses on file",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))
