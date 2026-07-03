"""Call analysis endpoints — run the LangGraph agent on a transcript.

Phase 5 runs the agent inline (synchronous request). Phase 8 will move this
to a Celery task with a job-id poll pattern. The request body shape is the
same in both cases so the dev console + extension don't need to change.
"""

import re
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.graph import run_analysis
from backend.agent.state import GenieState
from backend.db.models import Call, User
from backend.db.session import get_db
from backend.utils.auth import CurrentUser, CurrentUserId
from backend.utils.logging import logger

router = APIRouter(prefix="/analysis", tags=["analysis"])

MAX_TRANSCRIPT_CHARS = 100_000


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    transcript: str = Field(min_length=10, max_length=MAX_TRANSCRIPT_CHARS)
    platform: str = "manual"
    duration_secs: int = 0
    # Metrics — optional. If absent, derived crudely from the transcript text.
    talk_ratio_rep: Optional[float] = None
    talk_ratio_prospect: Optional[float] = None
    question_count: Optional[int] = None
    speaker_count: Optional[int] = None
    # When set, attach analysis to an existing Call row instead of creating one.
    call_id: Optional[uuid.UUID] = None


class AnalyzeResponse(BaseModel):
    analysis_id: Optional[str] = None
    call_id: str

    # meeting identity (NEW)
    call_title: str = ""
    prospect_name: str = ""

    # core classification + scoring
    call_type: str
    call_type_justification: str = ""
    methodology_id: str = "GENIE_v1"
    overall_score: float
    score_band: str
    score_justification: str = ""
    dimension_scores: list[dict]
    strengths: list[str]
    improvements: list[str]

    # structured signals (NEW)
    objections: list[dict] = []
    buying_signals: list[dict] = []
    competitors_mentioned: list[dict] = []

    # risk + next step
    next_step_quality: str
    next_step_action: str = ""
    next_step_owner: str = ""
    loss_risk_categories: list[str]

    # narrative
    ai_summary: str
    call_notes: str
    call_summary_bullets: list[str] = []
    key_quotes: list[dict] = []

    # action
    alert_level: str
    notification_sent: bool
    connector_results: list[dict] = []
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_LABEL_RE = re.compile(r"^(Rep|Prospect|Speaker_[A-Z]):\s*", re.MULTILINE)


def _derive_metrics_from_text(text: str) -> dict:
    """Cheap fallback when the caller doesn't pass metrics — count words per
    role label and tally '?' chars in Rep lines.
    """
    rep_words = 0
    prospect_words = 0
    rep_questions = 0
    for line in text.split("\n"):
        m = _LABEL_RE.match(line)
        if not m:
            continue
        role = m.group(1)
        body = line[m.end():]
        wc = len(body.split())
        if role == "Rep":
            rep_words += wc
            rep_questions += body.count("?")
        else:
            prospect_words += wc
    total = rep_words + prospect_words or 1
    return {
        "talk_ratio_rep": round(rep_words / total * 100, 1),
        "talk_ratio_prospect": round(prospect_words / total * 100, 1),
        "question_count": rep_questions,
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_transcript(
    body: AnalyzeRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnalyzeResponse:
    """Score a transcript using the user's KB. Synchronous for Phase 5."""
    # Derive missing metrics from the transcript text if not provided
    if (
        body.talk_ratio_rep is None
        or body.talk_ratio_prospect is None
        or body.question_count is None
    ):
        derived = _derive_metrics_from_text(body.transcript)
        rep_ratio = body.talk_ratio_rep if body.talk_ratio_rep is not None else derived["talk_ratio_rep"]
        prospect_ratio = body.talk_ratio_prospect if body.talk_ratio_prospect is not None else derived["talk_ratio_prospect"]
        questions = body.question_count if body.question_count is not None else derived["question_count"]
    else:
        rep_ratio = body.talk_ratio_rep
        prospect_ratio = body.talk_ratio_prospect
        questions = body.question_count

    # Find or create the Call row
    if body.call_id is not None:
        call = await db.get(Call, body.call_id)
        if call is None or call.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Call not found"
            )
    else:
        call = Call(
            user_id=current_user.id,
            platform=body.platform,
            duration_secs=body.duration_secs,
            transcript_raw=body.transcript,
            talk_ratio_rep=rep_ratio,
            talk_ratio_prospect=prospect_ratio,
            question_count=questions,
            speaker_count=body.speaker_count or 2,
        )
        db.add(call)
        await db.flush()
        await db.refresh(call)
        # The agent's write_db_node opens a fresh AsyncSession, so it can't see
        # the uncommitted Call row. Commit explicitly here so the FK is satisfied
        # before run_analysis fires.
        await db.commit()
    call_id = str(call.id)
    logger.info("analyze: starting agent run for call {} user {}", call_id, current_user.id)

    initial: GenieState = {
        "user_id": str(current_user.id),
        "call_id": call_id,
        "transcript_raw": body.transcript,
        "transcript_speakers": [],
        "talk_ratio_rep": float(rep_ratio),
        "talk_ratio_prospect": float(prospect_ratio),
        "question_count": int(questions),
        "platform": body.platform,
        "duration_secs": int(body.duration_secs),
        "alert_threshold_low": float(current_user.alert_threshold_low),
        "alert_threshold_high": float(current_user.alert_threshold_high),
        "notify_email": bool(current_user.notify_email),
        "notify_slack": bool(current_user.notify_slack),
        "manager_email": current_user.manager_email,
    }

    final = await run_analysis(initial)

    return AnalyzeResponse(
        analysis_id=final.get("analysis_id"),
        call_id=call_id,
        # meeting identity
        call_title=final.get("call_title") or "",
        prospect_name=final.get("prospect_name") or "",
        # core
        call_type=final.get("call_type", "Other"),
        call_type_justification=final.get("call_type_justification") or "",
        methodology_id=final.get("methodology_id") or "GENIE_v1",
        overall_score=float(final.get("overall_score") or 0.0),
        score_band=final.get("score_band") or "",
        score_justification=final.get("score_justification") or "",
        dimension_scores=final.get("dimension_scores") or [],
        strengths=final.get("strengths") or [],
        improvements=final.get("improvements") or [],
        # signals (NEW)
        objections=final.get("objections") or [],
        buying_signals=final.get("buying_signals") or [],
        competitors_mentioned=final.get("competitors_mentioned") or [],
        # risk + next step
        next_step_quality=final.get("next_step_quality") or "",
        next_step_action=final.get("next_step_action") or "",
        next_step_owner=final.get("next_step_owner") or "",
        loss_risk_categories=final.get("loss_risk_categories") or [],
        # narrative
        ai_summary=final.get("ai_summary") or "",
        call_notes=final.get("call_notes") or "",
        call_summary_bullets=final.get("call_summary_bullets") or [],
        key_quotes=final.get("key_quotes") or [],
        # action
        alert_level=final.get("alert_level", "none"),
        notification_sent=bool(final.get("notification_sent")),
        connector_results=final.get("extras_connector_results") or [],
        error=final.get("error"),
    )


# ---------------------------------------------------------------------------
# Async variant — submit a job, poll for the result (Phase 8)
# ---------------------------------------------------------------------------
class SubmitResponse(BaseModel):
    job_id: str
    state: str = "queued"


class JobStatusResponse(BaseModel):
    job_id: str
    state: str  # queued | running | done | failed
    step: str = ""
    progress: int = 0
    result: Optional[dict] = None
    error: Optional[str] = None


@router.post("/submit", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_analysis(
    body: AnalyzeRequest,
    current_user: CurrentUser,
) -> SubmitResponse:
    """Queue an analysis for background processing. Returns a job_id immediately.

    Poll GET /analysis/job/{job_id} for progress + result. This is the path the
    Chrome extension uses so it never holds a 40s HTTP connection open.
    """
    from backend.tasks.analysis_tasks import process_call_task
    from backend.tasks.celery_app import set_job_status

    async_result = process_call_task.delay(str(current_user.id), body.model_dump(mode="json"))
    job_id = async_result.id
    set_job_status(job_id, state="queued", step="submitted", progress=0)
    logger.info("submit_analysis queued job={} user={}", job_id, current_user.id)
    return SubmitResponse(job_id=job_id, state="queued")


@router.get("/job/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, current_user: CurrentUser) -> JobStatusResponse:
    """Poll a submitted analysis job. Returns live status + result when done."""
    from backend.tasks.celery_app import get_job_status

    status_ = get_job_status(job_id)
    if status_ is None:
        # Job id unknown or expired — surface as 404 so the client stops polling
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job not found or expired",
        )
    return JobStatusResponse(
        job_id=job_id,
        state=status_.get("state", "unknown"),
        step=status_.get("step", ""),
        progress=int(status_.get("progress") or 0),
        result=status_.get("result"),
        error=status_.get("error"),
    )


# ---------------------------------------------------------------------------
# History list + detail + re-dispatch (Phase 9)
# ---------------------------------------------------------------------------
class AnalysisListItem(BaseModel):
    analysis_id: str
    call_id: str
    created_at: str
    call_title: Optional[str] = None
    prospect_name: Optional[str] = None
    call_type: Optional[str] = None
    overall_score: float
    score_band: Optional[str] = None
    alert_level: str


class AnalysisListPage(BaseModel):
    items: list[AnalysisListItem]
    total: int
    limit: int
    offset: int


def _analysis_to_dict(a) -> dict:
    """Full serialization of an Analysis row for the detail endpoint."""
    return {
        "analysis_id": str(a.id),
        "call_id": str(a.call_id),
        "created_at": a.created_at.isoformat() if a.created_at else "",
        "call_title": a.call_title,
        "prospect_name": a.prospect_name,
        "call_type": a.call_type,
        "call_type_justification": a.call_type_justification,
        "methodology_id": a.methodology_id,
        "overall_score": a.overall_score,
        "score_band": a.score_band,
        "score_justification": a.score_justification,
        "dimension_scores": a.dimension_scores or [],
        "strengths": a.strengths or [],
        "improvements": a.improvements or [],
        "objections": a.objections or [],
        "buying_signals": a.buying_signals or [],
        "competitors_mentioned": a.competitors_mentioned or [],
        "loss_risk_categories": a.loss_risk_categories or [],
        "next_step_quality": a.next_step_quality,
        "next_step_action": a.next_step_action,
        "next_step_owner": a.next_step_owner,
        "ai_summary": a.ai_summary,
        "call_notes": a.call_notes,
        "call_summary_bullets": a.call_summary_bullets or [],
        "key_quotes": a.key_quotes or [],
        "alert_level": a.alert_level,
        "crm_written": a.crm_written,
        "notification_sent": a.notification_sent,
    }


@router.get("/history", response_model=AnalysisListPage)
async def analysis_history(
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
    offset: int = 0,
) -> AnalysisListPage:
    """Paginated list of the user's analyses (newest first)."""
    from sqlalchemy import func, select

    from backend.db.models import Analysis

    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    total = (
        await db.execute(
            select(func.count(Analysis.id)).where(Analysis.user_id == current_user_id)
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            select(Analysis)
            .where(Analysis.user_id == current_user_id)
            .order_by(Analysis.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    items = [
        AnalysisListItem(
            analysis_id=str(a.id),
            call_id=str(a.call_id),
            created_at=a.created_at.isoformat() if a.created_at else "",
            call_title=a.call_title,
            prospect_name=a.prospect_name,
            call_type=a.call_type,
            overall_score=a.overall_score,
            score_band=a.score_band,
            alert_level=a.alert_level,
        )
        for a in rows
    ]
    return AnalysisListPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{analysis_id}")
async def analysis_detail(
    analysis_id: uuid.UUID,
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Full detail for one analysis. 404 if not owned by the caller."""
    from sqlalchemy import select

    from backend.db.models import Analysis

    a = (
        await db.execute(select(Analysis).where(Analysis.id == analysis_id))
    ).scalar_one_or_none()
    if a is None or a.user_id != current_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")
    return _analysis_to_dict(a)


@router.post("/save/{analysis_id}")
async def save_analysis(
    analysis_id: uuid.UUID,
    current_user_id: CurrentUserId,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Re-dispatch an existing analysis to all configured connectors
    (Supabase + Notion + Sheets). Useful if a connector was added after the
    analysis ran, or a previous dispatch failed."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from backend.connectors.base import AnalysisPayload
    from backend.connectors.factory import dispatch_to_all
    from backend.db.models import Analysis, Call, User

    a = (
        await db.execute(select(Analysis).where(Analysis.id == analysis_id))
    ).scalar_one_or_none()
    if a is None or a.user_id != current_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    user_email = (
        await db.execute(select(User.email).where(User.id == current_user_id))
    ).scalar() or ""
    call = (
        await db.execute(select(Call).where(Call.id == a.call_id))
    ).scalar_one_or_none()

    payload = AnalysisPayload(
        analysis_id=str(a.id),
        call_id=str(a.call_id),
        user_id=str(current_user_id),
        user_email=user_email,
        call_type=a.call_type or "Other",
        call_type_justification=a.call_type_justification or "",
        methodology_id=a.methodology_id or "GENIE_v1",
        overall_score=float(a.overall_score or 0.0),
        score_band=a.score_band or "",
        score_justification=a.score_justification or "",
        dimension_scores=a.dimension_scores or [],
        strengths=a.strengths or [],
        improvements=a.improvements or [],
        objections=a.objections or [],
        buying_signals=a.buying_signals or [],
        competitors_mentioned=a.competitors_mentioned or [],
        next_step_quality=a.next_step_quality or "",
        next_step_action=a.next_step_action or "",
        next_step_owner=a.next_step_owner or "",
        loss_risk_categories=a.loss_risk_categories or [],
        ai_summary=a.ai_summary or "",
        call_notes=a.call_notes or "",
        call_summary_bullets=a.call_summary_bullets or [],
        key_quotes=a.key_quotes or [],
        alert_level=a.alert_level or "none",
        platform=call.platform if call else "manual",
        duration_secs=call.duration_secs if call else 0,
        talk_ratio_rep=call.talk_ratio_rep if call else 0.0,
        talk_ratio_prospect=call.talk_ratio_prospect if call else 0.0,
        question_count=call.question_count if call else 0,
        created_at_iso=(a.created_at.isoformat() if a.created_at else datetime.now(timezone.utc).isoformat()),
    )
    results = await dispatch_to_all(payload)
    if not a.crm_written:
        a.crm_written = True
    return {"analysis_id": str(a.id), "connector_results": [r.as_dict() for r in results]}
