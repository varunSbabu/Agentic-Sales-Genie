"""Retrieve_kb node — pull the user's scoring frameworks from ChromaDB."""

from backend.agent.state import GenieState
from backend.rag.retriever import EMPTY_KB_MESSAGE, retrieve_frameworks
from backend.utils.logging import logger

# Generic seed query that hits scoring-rubric content in any well-formed
# scoring framework. We deliberately do NOT use the call_type yet — that
# field doesn't exist until the classify node downstream.
_SEED_QUERY = (
    "sales call scoring framework dimensions rubric evaluation strengths "
    "improvements next step quality discovery active listening value "
    "articulation talk ratio objection handling"
)
_TOP_K = 8  # slightly larger than retriever default — scoring needs context


async def retrieve_kb_node(state: GenieState) -> dict:
    """Pull the user's framework chunks. Always succeeds; empty KB returns
    the EMPTY_KB_MESSAGE which the score node treats as a conservative-scoring
    signal.
    """
    user_id = state["user_id"]
    logger.info("retrieve_kb: user={} top_k={}", user_id, _TOP_K)
    try:
        frameworks = retrieve_frameworks(user_id, _SEED_QUERY, top_k=_TOP_K)
        chars = len(frameworks)
        logger.info(
            "retrieve_kb: returned {} chars ({})",
            chars,
            "empty placeholder" if frameworks == EMPTY_KB_MESSAGE else "content",
        )
        return {"retrieved_frameworks": frameworks}
    except Exception as exc:  # noqa: BLE001
        logger.exception("retrieve_kb failed: {}", exc)
        # Don't block the pipeline — degrade gracefully with the empty msg
        return {
            "retrieved_frameworks": EMPTY_KB_MESSAGE,
            "error": f"retrieve_kb failed: {exc}",
        }
