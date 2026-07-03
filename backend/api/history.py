"""History + aggregate-stats endpoints for the logged-in user."""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Analysis, Call
from backend.db.session import get_db
from backend.utils.auth import CurrentUserId

router = APIRouter(prefix="/history", tags=["history"])


class CallHistoryItem(BaseModel):
    call_id: str
    analysis_id: Optional[str] = None
    created_at: str
    platform: str
    duration_secs: int
    call_title: Optional[str] = None
    prospect_name: Optional[str] = None
    call_type: Optional[str] = None
    overall_score: Optional[float] = None
    score_band: Optional[str] = None
    alert_level: Optional[str] = None
    next_step_quality: Optional[str] = None


class CallHistoryPage(BaseModel):
    items: list[CallHistoryItem]
    total: int
    limit: int
    offset: int


class StatsOut(BaseModel):
    total_calls: int
    analyzed_calls: int
    avg_score: Optional[float] = None
    intervention_count: int
    coaching_count: int
    none_count: int
    by_call_type: dict[str, int]
    by_band: dict[str, int]


@router.get("/calls", response_model=CallHistoryPage)
async def list_calls(
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> CallHistoryPage:
    """Paginated call history joined with its analysis (if scored)."""
    total = (
        await db.execute(
            select(func.count(Call.id)).where(Call.user_id == current_user_id)
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            select(Call, Analysis)
            .outerjoin(Analysis, Analysis.call_id == Call.id)
            .where(Call.user_id == current_user_id)
            .order_by(Call.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    items: list[CallHistoryItem] = []
    for call, analysis in rows:
        items.append(
            CallHistoryItem(
                call_id=str(call.id),
                analysis_id=str(analysis.id) if analysis else None,
                created_at=call.created_at.isoformat() if call.created_at else "",
                platform=call.platform,
                duration_secs=call.duration_secs,
                call_title=(analysis.call_title if analysis else None),
                prospect_name=(analysis.prospect_name if analysis else None),
                call_type=(analysis.call_type if analysis else None),
                overall_score=(analysis.overall_score if analysis else None),
                score_band=(analysis.score_band if analysis else None),
                alert_level=(analysis.alert_level if analysis else None),
                next_step_quality=(analysis.next_step_quality if analysis else None),
            )
        )
    return CallHistoryPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StatsOut:
    """Aggregate stats across the user's analyses."""
    total_calls = (
        await db.execute(select(func.count(Call.id)).where(Call.user_id == current_user_id))
    ).scalar() or 0

    analyzed = (
        await db.execute(
            select(func.count(Analysis.id)).where(Analysis.user_id == current_user_id)
        )
    ).scalar() or 0

    avg_score = (
        await db.execute(
            select(func.avg(Analysis.overall_score)).where(
                Analysis.user_id == current_user_id, Analysis.overall_score > 0
            )
        )
    ).scalar()

    # Alert-level breakdown
    alert_rows = (
        await db.execute(
            select(Analysis.alert_level, func.count(Analysis.id))
            .where(Analysis.user_id == current_user_id)
            .group_by(Analysis.alert_level)
        )
    ).all()
    alert_map = {lvl or "none": cnt for lvl, cnt in alert_rows}

    # Call-type breakdown
    ct_rows = (
        await db.execute(
            select(Analysis.call_type, func.count(Analysis.id))
            .where(Analysis.user_id == current_user_id)
            .group_by(Analysis.call_type)
        )
    ).all()
    by_call_type = {ct or "Unknown": cnt for ct, cnt in ct_rows}

    # Band breakdown
    band_rows = (
        await db.execute(
            select(Analysis.score_band, func.count(Analysis.id))
            .where(Analysis.user_id == current_user_id)
            .group_by(Analysis.score_band)
        )
    ).all()
    by_band = {b or "Unscored": cnt for b, cnt in band_rows}

    return StatsOut(
        total_calls=total_calls,
        analyzed_calls=analyzed,
        avg_score=round(float(avg_score), 2) if avg_score is not None else None,
        intervention_count=alert_map.get("intervention", 0),
        coaching_count=alert_map.get("coaching", 0),
        none_count=alert_map.get("none", 0),
        by_call_type=by_call_type,
        by_band=by_band,
    )
