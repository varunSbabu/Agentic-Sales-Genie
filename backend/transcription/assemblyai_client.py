"""AssemblyAI post-call transcription client.

Uploads an audio blob, requests transcription with speaker diarization, polls
until complete, and returns structured utterances. AssemblyAI's REST surface
is small enough that pulling in the official SDK adds little value — we go
direct via httpx with explicit retry/timeout semantics.
"""

import asyncio
from dataclasses import dataclass

import httpx

from backend.config import settings
from backend.utils.logging import logger

API_BASE = "https://api.assemblyai.com/v2"
POLL_INTERVAL_SECONDS = 3.0
POLL_MAX_ATTEMPTS = 120  # ~6 minutes
UPLOAD_TIMEOUT_SECONDS = 120.0


class TranscriptionError(Exception):
    """Raised when transcription fails for any reason."""


@dataclass
class Utterance:
    speaker: str  # "A", "B", "C" — opaque labels from AssemblyAI's diarization
    text: str
    start_ms: int
    end_ms: int
    confidence: float

    def to_dict(self) -> dict:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "confidence": self.confidence,
        }


def _require_api_key() -> str:
    if not settings.assemblyai_api_key:
        raise TranscriptionError(
            "ASSEMBLYAI_API_KEY not set. Sign up free at https://www.assemblyai.com/ "
            "(3 hours/month free), then add the key to .env and restart the backend."
        )
    return settings.assemblyai_api_key


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str = "audio.webm",
    speakers_expected: int = 2,
) -> list[Utterance]:
    """Upload audio → request diarized transcription → poll → return utterances."""
    api_key = _require_api_key()
    headers = {"authorization": api_key}

    if not audio_bytes:
        raise TranscriptionError("audio_bytes is empty")

    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
        # 1. Upload the audio bytes (AssemblyAI then references it by URL)
        logger.info(
            "uploading {} ({} bytes) to AssemblyAI", filename, len(audio_bytes)
        )
        try:
            upload_resp = await client.post(
                f"{API_BASE}/upload", content=audio_bytes, headers=headers
            )
            upload_resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"upload failed: {exc}") from exc
        audio_url = upload_resp.json()["upload_url"]

        # 2. Request transcription with diarization
        try:
            tx_resp = await client.post(
                f"{API_BASE}/transcript",
                json={
                    "audio_url": audio_url,
                    "speaker_labels": True,
                    "speakers_expected": speakers_expected,
                    "punctuate": True,
                    "format_text": True,
                },
                headers=headers,
            )
            tx_resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TranscriptionError(f"transcript request failed: {exc}") from exc
        transcript_id = tx_resp.json()["id"]
        logger.info("AssemblyAI transcript queued: {}", transcript_id)

        # 3. Poll until completed/error
        poll_url = f"{API_BASE}/transcript/{transcript_id}"
        for attempt in range(POLL_MAX_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            try:
                poll_resp = await client.get(poll_url, headers=headers)
                poll_resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("poll attempt {} failed: {}", attempt, exc)
                continue
            data = poll_resp.json()
            status_ = data.get("status")
            if status_ == "completed":
                utterances_raw = data.get("utterances") or []
                logger.info(
                    "transcription complete: {} utterances, {} speakers",
                    len(utterances_raw),
                    len({u.get("speaker") for u in utterances_raw}),
                )
                return [
                    Utterance(
                        speaker=u.get("speaker", "?"),
                        text=(u.get("text") or "").strip(),
                        start_ms=int(u.get("start", 0)),
                        end_ms=int(u.get("end", 0)),
                        confidence=float(u.get("confidence", 0.0)),
                    )
                    for u in utterances_raw
                    if (u.get("text") or "").strip()
                ]
            if status_ == "error":
                raise TranscriptionError(
                    f"AssemblyAI error: {data.get('error', 'unknown')}"
                )
            # queued / processing — keep polling

    raise TranscriptionError(
        f"timed out after {POLL_MAX_ATTEMPTS * POLL_INTERVAL_SECONDS}s"
    )
