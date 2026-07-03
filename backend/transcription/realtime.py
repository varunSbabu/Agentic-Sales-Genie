"""Real-time WebSocket relay for AssemblyAI streaming transcription.

The flow at runtime (wired up in Phase 9):

  Extension MediaRecorder
      → POST /recording/chunk (base64 audio chunks)
          → this relay
              → AssemblyAI realtime WebSocket
              ← partial / final transcripts
          ← SSE /recording/transcript/stream
      ← Live transcript shown in the sidebar

For Phase 4 we expose the client wrapper. The Phase 9 endpoints will plug an
async iterator of audio chunks into `stream_transcribe()` and forward yielded
PartialTranscript objects to the SSE stream.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator

import httpx
import websockets

from backend.config import settings
from backend.utils.logging import logger

REALTIME_WS_URL = "wss://api.assemblyai.com/v2/realtime/ws"
TOKEN_URL = "https://api.assemblyai.com/v2/realtime/token"


@dataclass
class PartialTranscript:
    text: str
    is_final: bool
    confidence: float = 0.0


def _require_api_key() -> str:
    if not settings.assemblyai_api_key:
        raise RuntimeError(
            "ASSEMBLYAI_API_KEY not set — required for realtime transcription."
        )
    return settings.assemblyai_api_key


async def _get_temp_token(expires_in: int = 3600) -> str:
    """Mint a short-lived realtime token. The full API key never reaches the browser."""
    api_key = _require_api_key()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            TOKEN_URL,
            headers={"authorization": api_key},
            json={"expires_in": expires_in},
        )
        resp.raise_for_status()
        return resp.json()["token"]


async def stream_transcribe(
    audio_chunks: AsyncIterator[bytes],
    *,
    sample_rate: int = 16000,
) -> AsyncIterator[PartialTranscript]:
    """Forward audio chunks to AssemblyAI realtime, yield partial transcripts.

    `audio_chunks` is an async iterator of raw PCM bytes (16-bit signed, mono,
    sample_rate Hz). The Chrome extension's MediaRecorder produces Opus/WebM;
    Phase 9 will transcode to PCM before passing chunks here.
    """
    token = await _get_temp_token()
    url = f"{REALTIME_WS_URL}?sample_rate={sample_rate}&token={token}"

    async with websockets.connect(url) as ws:
        logger.info("realtime WS connected (sample_rate={})", sample_rate)

        async def send_audio() -> None:
            try:
                async for chunk in audio_chunks:
                    if chunk:
                        await ws.send(chunk)
            finally:
                # Tell AssemblyAI we're done
                try:
                    await ws.send(json.dumps({"terminate_session": True}))
                except Exception:  # noqa: BLE001 — best-effort close
                    pass

        send_task = asyncio.create_task(send_audio())
        try:
            async for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("message_type")
                if mtype in ("PartialTranscript", "FinalTranscript"):
                    yield PartialTranscript(
                        text=msg.get("text", ""),
                        is_final=mtype == "FinalTranscript",
                        confidence=float(msg.get("confidence", 0.0)),
                    )
                elif mtype == "SessionTerminated":
                    break
        finally:
            send_task.cancel()
            try:
                await send_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        logger.info("realtime WS closed")
