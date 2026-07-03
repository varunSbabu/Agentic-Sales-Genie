"""Connector tests — base contract, factory selection, graceful failures, fan-out.

Notion/Sheets live calls need real tokens, so those are exercised only via the
'not configured' path (which must fail gracefully, not raise). The factory
fan-out is tested with a fake connector so no network is touched.
"""

import uuid

import pytest

from backend.connectors.base import AnalysisPayload, BaseConnector, ConnectorResult


def _payload(user_id="u1"):
    return AnalysisPayload(
        analysis_id=str(uuid.uuid4()),
        call_id=str(uuid.uuid4()),
        user_id=user_id,
        user_email="x@y.com",
        call_type="Discovery",
        call_type_justification="",
        methodology_id="GENIE_v1",
        overall_score=3.5,
        score_band="SOLID",
        score_justification="",
        dimension_scores=[],
        strengths=[], improvements=[], objections=[], buying_signals=[],
        competitors_mentioned=[], next_step_quality="ADVANCED",
        next_step_action="", next_step_owner="", loss_risk_categories=[],
        ai_summary="", call_notes="", call_summary_bullets=[], key_quotes=[],
        alert_level="none",
    )


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------
def test_connector_result_serializes():
    r = ConnectorResult(connector="notion", ok=True, detail="page created", external_url="http://x")
    d = r.as_dict()
    assert d["connector"] == "notion" and d["ok"] is True and d["external_url"] == "http://x"


# ---------------------------------------------------------------------------
# Notion / Sheets graceful failure when not configured (no network)
# ---------------------------------------------------------------------------
async def test_notion_not_configured(db_up):
    from backend.connectors.notion_connector import NotionConnector
    # A random user with no integration row → returns ok=False, no raise
    res = await NotionConnector().write_analysis(_payload(user_id=str(uuid.uuid4())))
    assert res.ok is False
    assert "not configured" in (res.error or "")


async def test_sheets_not_configured(db_up):
    from backend.connectors.sheets_connector import SheetsConnector
    res = await SheetsConnector().write_analysis(_payload(user_id=str(uuid.uuid4())))
    assert res.ok is False
    assert "not configured" in (res.error or "")


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------
async def test_factory_defaults_to_supabase_only(db_up):
    from backend.connectors.factory import get_connectors
    connectors = await get_connectors(str(uuid.uuid4()))  # no integration row
    names = [c.name for c in connectors]
    assert names == ["supabase"]


# ---------------------------------------------------------------------------
# Fan-out: one failing connector must not sink the others
# ---------------------------------------------------------------------------
async def test_dispatch_isolates_failures(monkeypatch):
    import backend.connectors.factory as factory

    class OkConnector(BaseConnector):
        name = "ok"
        async def write_analysis(self, payload): return ConnectorResult(connector="ok", ok=True)
        async def test_connection(self, user_id): return ConnectorResult(connector="ok", ok=True)

    class BoomConnector(BaseConnector):
        name = "boom"
        async def write_analysis(self, payload): raise RuntimeError("kaboom")
        async def test_connection(self, user_id): return ConnectorResult(connector="boom", ok=False)

    async def fake_get_connectors(user_id):
        return [OkConnector(), BoomConnector()]

    monkeypatch.setattr(factory, "get_connectors", fake_get_connectors)
    results = await factory.dispatch_to_all(_payload())
    by_name = {r.connector: r for r in results}
    assert by_name["ok"].ok is True
    assert by_name["boom"].ok is False  # exception converted to a result, not raised
