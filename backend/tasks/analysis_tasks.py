"""Celery task: run the full analysis pipeline in the background.

The LangGraph agent is async; Celery workers are sync. We bridge with
asyncio.run() inside the task. Job progress is written to Redis at each
milestone so the API's poll endpoint can surface it.
"""

from __future__ import annotations

import re
import uuid

from backend.tasks.celery_app import celery_app, run_async, set_job_status
from backend.utils.logging import logger

_LABEL_RE = re.compile(r"^(Rep|Prospect|Speaker_[A-Z]):\s*", re.MULTILINE)


def _derive_metrics(text: str) -> dict:
    """Cheap talk-ratio + question count from a speaker-labelled transcript.
    Mirrors the logic in the sync analysis endpoint so async results match."""
    rep_words = prospect_words = rep_questions = 0
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


async def _run_pipeline(job_id: str, user_id: str, body: dict) -> dict:
    """Create the Call row, run the agent, return the final state."""
    # Imports are inside the function so the celery worker doesn't import the
    # whole FastAPI app graph at module load (keeps the worker lean).
    from sqlalchemy import select

    from backend.agent.graph import run_analysis
    from backend.agent.state import GenieState
    from backend.db.models import Call, User
    from backend.db.session import AsyncSessionLocal

    transcript = body["transcript"]
    metrics = _derive_metrics(transcript)
    rep_ratio = body.get("talk_ratio_rep")
    if rep_ratio is None:
        rep_ratio = metrics["talk_ratio_rep"]
    prospect_ratio = body.get("talk_ratio_prospect")
    if prospect_ratio is None:
        prospect_ratio = metrics["talk_ratio_prospect"]
    questions = body.get("question_count")
    if questions is None:
        questions = metrics["question_count"]

    set_job_status(job_id, state="running", step="creating call record", progress=15)

    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"user {user_id} not found")

        call = Call(
            user_id=user.id,
            platform=body.get("platform", "manual"),
            duration_secs=int(body.get("duration_secs") or 0),
            transcript_raw=transcript,
            talk_ratio_rep=float(rep_ratio),
            talk_ratio_prospect=float(prospect_ratio),
            question_count=int(questions),
            speaker_count=int(body.get("speaker_count") or 2),
        )
        session.add(call)
        await session.flush()
        await session.refresh(call)
        call_id = str(call.id)
        # Commit so the agent's fresh session sees the FK
        await session.commit()

        prefs = {
            "alert_threshold_low": float(user.alert_threshold_low),
            "alert_threshold_high": float(user.alert_threshold_high),
            "notify_email": bool(user.notify_email),
            "notify_slack": bool(user.notify_slack),
            "manager_email": user.manager_email,
        }

    set_job_status(job_id, state="running", step="running agent (score + coach + alert)", progress=40)

    initial: GenieState = {
        "user_id": user_id,
        "call_id": call_id,
        "transcript_raw": transcript,
        "transcript_speakers": [],
        "talk_ratio_rep": float(rep_ratio),
        "talk_ratio_prospect": float(prospect_ratio),
        "question_count": int(questions),
        "platform": body.get("platform", "manual"),
        "duration_secs": int(body.get("duration_secs") or 0),
        **prefs,
    }

    final = await run_analysis(initial)

    set_job_status(job_id, state="running", step="finalizing", progress=90)

    return {
        "analysis_id": final.get("analysis_id"),
        "call_id": call_id,
        "call_title": final.get("call_title") or "",
        "prospect_name": final.get("prospect_name") or "",
        "call_type": final.get("call_type", "Other"),
        "overall_score": float(final.get("overall_score") or 0.0),
        "score_band": final.get("score_band") or "",
        "score_justification": final.get("score_justification") or "",
        "dimension_scores": final.get("dimension_scores") or [],
        "strengths": final.get("strengths") or [],
        "improvements": final.get("improvements") or [],
        "objections": final.get("objections") or [],
        "buying_signals": final.get("buying_signals") or [],
        "competitors_mentioned": final.get("competitors_mentioned") or [],
        "next_step_quality": final.get("next_step_quality") or "",
        "next_step_action": final.get("next_step_action") or "",
        "next_step_owner": final.get("next_step_owner") or "",
        "loss_risk_categories": final.get("loss_risk_categories") or [],
        "ai_summary": final.get("ai_summary") or "",
        "call_notes": final.get("call_notes") or "",
        "call_summary_bullets": final.get("call_summary_bullets") or [],
        "key_quotes": final.get("key_quotes") or [],
        "alert_level": final.get("alert_level", "none"),
        "notification_sent": bool(final.get("notification_sent")),
        "connector_results": final.get("extras_connector_results") or [],
        "error": final.get("error"),
    }


@celery_app.task(name="analysis.process_call", bind=True)
def process_call_task(self, user_id: str, body: dict) -> dict:
    """Entry point Celery invokes. `body` is the AnalyzeRequest as a dict."""
    job_id = self.request.id
    logger.info("process_call_task START job={} user={}", job_id, user_id)
    set_job_status(job_id, state="queued", step="picked up by worker", progress=5)
    try:
        result = run_async(_run_pipeline(job_id, user_id, body))
        if result.get("error"):
            set_job_status(
                job_id, state="failed", step="agent error", progress=100,
                result=result, error=result["error"],
            )
        else:
            set_job_status(
                job_id, state="done", step="complete", progress=100, result=result,
            )
        logger.info("process_call_task DONE job={} score={}", job_id, result.get("overall_score"))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("process_call_task FAILED job={}: {}", job_id, exc)
        set_job_status(job_id, state="failed", step="exception", progress=100, error=str(exc))
        raise
