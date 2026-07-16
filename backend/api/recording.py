"""Recording endpoints — the audio pipeline the Chrome extension drives.

Flow:
  1. POST /recording/start           → returns session_id, opens a disk buffer
  2. POST /recording/chunk (xN)      → base64 audio chunks appended to the buffer
  3. POST /recording/stop            → assemble → AssemblyAI transcription →
                                        queue the analysis Celery job → return job_id
  4. GET  /recording/transcript/stream → SSE of the analysis job's live progress
                                        (the extension can also poll /analysis/job/{id})

Audio buffering: for dev we spool chunks to a per-session temp file on disk.
Production should stream to Cloudflare R2 instead (see backend/storage/r2.py);
chunks are never persisted in Redis, per the spec. The disk buffer is deleted
after transcription completes.

Rate-limited via slowapi (10 req/hour/IP on start+stop) as the spec requires.
"""

import base64
import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.transcription.assemblyai_client import TranscriptionError, transcribe_audio
from backend.transcription.processor import process_utterances
from backend.utils.auth import CurrentUser, CurrentUserId
from backend.utils.logging import logger

router = APIRouter(prefix="/recording", tags=["recording"])
limiter = Limiter(key_func=get_remote_address)

# Per-session audio spool dir. In prod this becomes an R2 multipart upload.
_SPOOL_ROOT = Path("/tmp/sg_recordings")
_SPOOL_ROOT.mkdir(parents=True, exist_ok=True)

MAX_CHUNK_BYTES = 5 * 1024 * 1024      # 5 MB per chunk
MAX_SESSION_BYTES = 200 * 1024 * 1024  # 200 MB per recording


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class StartRequest(BaseModel):
    platform: str = "extension"


class StartResponse(BaseModel):
    session_id: str


class ChunkRequest(BaseModel):
    session_id: str
    # base64-encoded audio bytes (webm/opus from MediaRecorder)
    chunk_base64: str = Field(..., description="base64 audio chunk")
    seq: int = 0


class ChunkResponse(BaseModel):
    session_id: str
    seq: int
    bytes_received: int
    total_bytes: int


class StopRequest(BaseModel):
    session_id: str
    platform: str = "extension"
    duration_secs: int = 0


class StopResponse(BaseModel):
    session_id: str
    job_id: Optional[str] = None
    transcript_preview: str = ""
    utterance_count: int = 0
    talk_ratio_rep: float = 0.0
    talk_ratio_prospect: float = 0.0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _session_dir(user_id, session_id: str) -> Path:
    # Namespaced by user so one user can't touch another's spool
    return _SPOOL_ROOT / str(user_id) / session_id


def _audio_path(user_id, session_id: str) -> Path:
    return _session_dir(user_id, session_id) / "audio.webm"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/start", response_model=StartResponse)
@limiter.limit("10/hour")
async def start_recording(
    request: Request,
    body: StartRequest,
    current_user_id: CurrentUserId,
) -> StartResponse:
    session_id = uuid.uuid4().hex
    d = _session_dir(current_user_id, session_id)
    d.mkdir(parents=True, exist_ok=True)
    # touch the audio file so appends work
    _audio_path(current_user_id, session_id).touch()
    logger.info("recording start: user={} session={}", current_user_id, session_id)
    return StartResponse(session_id=session_id)


@router.post("/chunk", response_model=ChunkResponse)
async def upload_chunk(
    body: ChunkRequest,
    current_user_id: CurrentUserId,
) -> ChunkResponse:
    path = _audio_path(current_user_id, body.session_id)
    if not path.parent.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown session — call /recording/start first",
        )
    try:
        raw = base64.b64decode(body.chunk_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid base64: {exc}"
        )
    if len(raw) > MAX_CHUNK_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"chunk exceeds {MAX_CHUNK_BYTES} bytes",
        )

    total = path.stat().st_size + len(raw)
    if total > MAX_SESSION_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"recording exceeds {MAX_SESSION_BYTES} bytes",
        )
    with path.open("ab") as f:
        f.write(raw)
    return ChunkResponse(
        session_id=body.session_id,
        seq=body.seq,
        bytes_received=len(raw),
        total_bytes=total,
    )


@router.post("/stop", response_model=StopResponse)
@limiter.limit("40/hour")
async def stop_recording(
    request: Request,
    body: StopRequest,
    current_user: CurrentUser,
) -> StopResponse:
    """Assemble the buffered audio → transcribe → queue analysis. Returns job_id."""
    user_id = current_user.id
    path = _audio_path(user_id, body.session_id)
    if not path.exists() or path.stat().st_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no audio captured for this session",
        )

    audio_bytes = path.read_bytes()
    logger.info(
        "recording stop: user={} session={} bytes={}",
        user_id, body.session_id, len(audio_bytes),
    )

    # 1. Transcribe (post-call, diarized)
    try:
        utterances = await transcribe_audio(audio_bytes, filename="recording.webm")
    except TranscriptionError as exc:
        return StopResponse(session_id=body.session_id, error=str(exc))
    finally:
        # Clean up the spool regardless — audio is not persisted locally
        shutil.rmtree(_session_dir(user_id, body.session_id), ignore_errors=True)

    result = process_utterances(utterances)
    if not result.formatted_text.strip():
        return StopResponse(
            session_id=body.session_id,
            error="transcription produced no text",
        )

    # 2. Queue the analysis job (async — extension polls /analysis/job/{id})
    from backend.tasks.analysis_tasks import process_call_task
    from backend.tasks.celery_app import set_job_status

    analyze_body = {
        "transcript": result.formatted_text,
        "platform": body.platform,
        "duration_secs": body.duration_secs,
        "talk_ratio_rep": result.talk_ratio_rep,
        "talk_ratio_prospect": result.talk_ratio_prospect,
        "question_count": result.question_count,
        "speaker_count": result.speaker_count,
    }
    async_result = process_call_task.delay(str(user_id), analyze_body)
    set_job_status(async_result.id, state="queued", step="submitted from recording", progress=0)

    preview = result.formatted_text[:400]
    return StopResponse(
        session_id=body.session_id,
        job_id=async_result.id,
        transcript_preview=preview,
        utterance_count=len(result.speakers),
        talk_ratio_rep=result.talk_ratio_rep,
        talk_ratio_prospect=result.talk_ratio_prospect,
    )


@router.get("/transcript/stream")
async def transcript_stream(
    job_id: str,
    current_user_id: CurrentUserId,
) -> StreamingResponse:
    """Server-Sent Events stream of an analysis job's live progress.

    The extension opens this after /recording/stop returns a job_id. Each event
    is the job-status JSON; the stream closes when the job reaches done/failed.
    """
    import asyncio

    from backend.tasks.celery_app import get_job_status

    async def event_gen():
        last = None
        for _ in range(180):  # ~6 min cap at 2s intervals
            status_ = get_job_status(job_id)
            if status_ is None:
                yield f"data: {json.dumps({'state': 'unknown', 'job_id': job_id})}\n\n"
                return
            payload = json.dumps(status_)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if status_.get("state") in ("done", "failed"):
                return
            await asyncio.sleep(2)
        yield f"data: {json.dumps({'state': 'timeout', 'job_id': job_id})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable proxy buffering for SSE
        },
    )
