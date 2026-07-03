"""Persist the agent's analysis to Supabase.

The analyses table has a UNIQUE(call_id) constraint, so this is upsert-like:
look for an existing row first and update, otherwise create. Connectors in
Phase 6 fire AFTER this lands the row so they can reference analysis_id.

The set of fields written here must stay in sync with the columns added by
migration 0002_curated_analysis_fields.
"""

import uuid

from sqlalchemy import select

from backend.agent.state import GenieState
from backend.db.models import Analysis
from backend.db.session import AsyncSessionLocal
from backend.utils.logging import logger


def _build_payload(state: GenieState) -> dict:
    """Map agent state fields → DB column values. Centralised so create + update
    paths can't drift."""
    return {
        # meeting identity
        "call_title": state.get("call_title"),
        "prospect_name": state.get("prospect_name"),
        # core
        "call_type": state.get("call_type"),
        "call_type_justification": state.get("call_type_justification"),
        "methodology_id": state.get("methodology_id") or "GENIE_v1",
        "overall_score": float(state.get("overall_score") or 0.0),
        "score_band": state.get("score_band"),
        "score_justification": state.get("score_justification"),
        "dimension_scores": state.get("dimension_scores") or [],
        "strengths": state.get("strengths") or [],
        "improvements": state.get("improvements") or [],
        # signals
        "objections": state.get("objections") or [],
        "buying_signals": state.get("buying_signals") or [],
        "competitors_mentioned": state.get("competitors_mentioned") or [],
        # risk + next step
        "loss_risk_categories": state.get("loss_risk_categories") or [],
        "next_step_quality": state.get("next_step_quality"),
        "next_step_action": state.get("next_step_action"),
        "next_step_owner": state.get("next_step_owner"),
        # narrative
        "ai_summary": state.get("ai_summary"),
        "call_notes": state.get("call_notes"),
        "call_summary_bullets": state.get("call_summary_bullets") or [],
        "key_quotes": state.get("key_quotes") or [],
        # action
        "alert_level": state.get("alert_level", "none"),
    }


async def write_db_node(state: GenieState) -> dict:
    logger.info(
        "write_db: call_id={} score={:.2f} band={} alert={}",
        state.get("call_id"),
        float(state.get("overall_score") or 0.0),
        state.get("score_band") or "?",
        state.get("alert_level") or "none",
    )
    if state.get("error"):
        logger.warning("write_db: skipping due to upstream error: {}", state["error"])
        return {"crm_written": False}

    try:
        async with AsyncSessionLocal() as session:
            call_id = uuid.UUID(str(state["call_id"]))
            user_id = uuid.UUID(str(state["user_id"]))
            payload = _build_payload(state)

            existing = (
                await session.execute(
                    select(Analysis).where(Analysis.call_id == call_id)
                )
            ).scalar_one_or_none()

            if existing is None:
                analysis = Analysis(
                    call_id=call_id,
                    user_id=user_id,
                    crm_written=False,
                    notification_sent=False,
                    **payload,
                )
                session.add(analysis)
                await session.flush()
                await session.refresh(analysis)
                logger.info("write_db: created Analysis {}", analysis.id)
            else:
                for k, v in payload.items():
                    setattr(existing, k, v)
                analysis = existing
                logger.info("write_db: updated Analysis {}", analysis.id)

            await session.commit()
            return {"analysis_id": str(analysis.id), "crm_written": True}
    except Exception as exc:  # noqa: BLE001
        logger.exception("write_db failed: {}", exc)
        return {"crm_written": False, "error": f"write_db failed: {exc}"}
