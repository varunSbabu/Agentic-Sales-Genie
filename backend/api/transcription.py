"""Transcription endpoints — file upload variant for Phase 4 testing.

Phase 9 will add the realtime endpoints (/recording/start, /recording/chunk,
/recording/transcript/stream, /recording/stop) that the Chrome extension uses.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from backend.transcription.assemblyai_client import TranscriptionError, transcribe_audio
from backend.transcription.processor import process_utterances
from backend.utils.auth import CurrentUserId
from backend.utils.logging import logger

router = APIRouter(prefix="/transcription", tags=["transcription"])

MAX_AUDIO_BYTES = 200 * 1024 * 1024  # 200 MB
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac", ".mp4"}


class TranscriptOut(BaseModel):
    formatted_text: str
    talk_ratio_rep: float
    talk_ratio_prospect: float
    question_count: int
    speaker_count: int  # logical: 1 (rep) or 2 (rep + prospect)
    raw_speaker_count: int  # what AssemblyAI returned
    utterance_count: int
    rep_speaker_label: str
    role_map: dict[str, str]
    rep_detection_confidence: float
    rep_detection_signals: dict[str, dict[str, int]]
    rep_detection_overridden: bool
    speakers: list[dict]


@router.post("/file", response_model=TranscriptOut)
async def transcribe_uploaded_file(
    current_user_id: CurrentUserId,
    file: UploadFile = File(...),
    rep_speaker: Optional[str] = Form(
        None,
        description=(
            "Optional explicit Rep speaker label override (e.g. 'A', 'B'). "
            "If not provided, the speaker who asks the most questions is "
            "assigned the Rep role and all others become Prospect."
        ),
    ),
) -> TranscriptOut:
    """Upload an audio file → AssemblyAI post-call → return Genie-shaped transcript."""
    filename = file.filename or "audio"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext and ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unsupported audio format {ext}; accepted: "
                f"{sorted(ALLOWED_AUDIO_EXTENSIONS)}"
            ),
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty file"
        )
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"audio exceeds {MAX_AUDIO_BYTES} bytes ({len(content)} sent)",
        )

    logger.info(
        "user {} uploaded {} for transcription ({} bytes, rep_hint={!r})",
        current_user_id,
        filename,
        len(content),
        rep_speaker,
    )
    try:
        utterances = await transcribe_audio(content, filename=filename)
    except TranscriptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        )

    result = process_utterances(utterances, rep_speaker_hint=rep_speaker)
    return TranscriptOut(
        formatted_text=result.formatted_text,
        talk_ratio_rep=result.talk_ratio_rep,
        talk_ratio_prospect=result.talk_ratio_prospect,
        question_count=result.question_count,
        speaker_count=result.speaker_count,
        raw_speaker_count=result.raw_speaker_count,
        utterance_count=len(result.speakers),
        rep_speaker_label=result.rep_speaker_label,
        role_map=result.role_map,
        rep_detection_confidence=result.rep_detection_confidence,
        rep_detection_signals=result.rep_detection_signals,
        rep_detection_overridden=result.rep_detection_overridden,
        speakers=result.speakers,
    )
