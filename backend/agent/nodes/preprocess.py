"""Preprocess node — validate inputs and set defaults that later nodes assume."""

from backend.agent.state import GenieState
from backend.rag.vectorstore import collection_name_for
from backend.utils.logging import logger
from backend.utils.validators import truncate_transcript


async def preprocess_node(state: GenieState) -> dict:
    """Sanity-check the incoming state and prep derived fields.

    - Caps transcript size before LLM injection (defends $$ and rate limits).
    - Sets `kb_collection` from `user_id` so retrieve_kb doesn't need to know
      the naming convention.
    - Initialises action-tracking booleans so downstream nodes can set them
      without KeyError.
    """
    logger.info("preprocess: call_id={} user={}", state.get("call_id"), state.get("user_id"))
    try:
        transcript = state.get("transcript_raw") or ""
        if not transcript.strip():
            return {"error": "transcript_raw is empty — nothing to analyse"}

        return {
            "transcript_raw": truncate_transcript(transcript),
            "kb_collection": collection_name_for(state["user_id"]),
            "crm_written": False,
            "notification_sent": False,
            "alert_level": "none",
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("preprocess failed: {}", exc)
        return {"error": f"preprocess failed: {exc}"}
