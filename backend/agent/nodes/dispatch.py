"""Dispatch node — fans the analysis out to Notion / Sheets after Supabase writes.

Runs after write_db_node. Supabase is always re-verified by SupabaseConnector,
plus any other configured connectors fire in parallel via asyncio.gather. All
errors are converted to ConnectorResult — this node never crashes the graph.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from backend.agent.state import GenieState
from backend.connectors.base import AnalysisPayload
from backend.connectors.factory import dispatch_to_all
from backend.db.models import Call, User
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger


async def _build_payload(state: GenieState) -> AnalysisPayload | None:
    """Pull the user email + call metadata we need to render the payload."""
    if not state.get("analysis_id"):
        return None
    try:
        async with AsyncSessionLocal() as session:
            r = await session.execute(
                select(User.email).where(User.id == uuid.UUID(str(state["user_id"])))
            )
            email = r.scalar() or ""
            r = await session.execute(
                select(Call.created_at).where(Call.id == uuid.UUID(str(state["call_id"])))
            )
            created_at = r.scalar()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dispatch: failed to load aux data: {}", exc)
        email = ""
        created_at = None

    return AnalysisPayload(
        analysis_id=str(state["analysis_id"]),
        call_id=str(state["call_id"]),
        user_id=str(state["user_id"]),
        user_email=email,
        call_type=state.get("call_type") or "Other",
        call_type_justification=state.get("call_type_justification") or "",
        methodology_id=state.get("methodology_id") or "GENIE_v1",
        overall_score=float(state.get("overall_score") or 0.0),
        score_band=state.get("score_band") or "",
        score_justification=state.get("score_justification") or "",
        dimension_scores=state.get("dimension_scores") or [],
        strengths=state.get("strengths") or [],
        improvements=state.get("improvements") or [],
        objections=state.get("objections") or [],
        buying_signals=state.get("buying_signals") or [],
        competitors_mentioned=state.get("competitors_mentioned") or [],
        next_step_quality=state.get("next_step_quality") or "",
        next_step_action=state.get("next_step_action") or "",
        next_step_owner=state.get("next_step_owner") or "",
        loss_risk_categories=state.get("loss_risk_categories") or [],
        ai_summary=state.get("ai_summary") or "",
        call_notes=state.get("call_notes") or "",
        call_summary_bullets=state.get("call_summary_bullets") or [],
        key_quotes=state.get("key_quotes") or [],
        alert_level=state.get("alert_level") or "none",
        platform=state.get("platform") or "manual",
        duration_secs=int(state.get("duration_secs") or 0),
        talk_ratio_rep=float(state.get("talk_ratio_rep") or 0.0),
        talk_ratio_prospect=float(state.get("talk_ratio_prospect") or 0.0),
        question_count=int(state.get("question_count") or 0),
        created_at_iso=(created_at.isoformat() if created_at else datetime.now(timezone.utc).isoformat()),
    )


async def dispatch_connectors_node(state: GenieState) -> dict:
    logger.info("dispatch: call_id={} analysis_id={}", state.get("call_id"), state.get("analysis_id"))
    # If write_db failed there's nothing to dispatch
    if not state.get("analysis_id") or state.get("error"):
        return {"crm_written": bool(state.get("crm_written"))}

    payload = await _build_payload(state)
    if payload is None:
        return {"crm_written": bool(state.get("crm_written"))}

    try:
        results = await dispatch_to_all(payload)
        summary = ", ".join(f"{r.connector}={'ok' if r.ok else 'fail'}" for r in results)
        logger.info("dispatch: {}", summary)
        return {
            "crm_written": True,
            "extras_connector_results": [r.as_dict() for r in results],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("dispatch failed: {}", exc)
        return {"crm_written": True, "error": f"dispatch failed: {exc}"}
