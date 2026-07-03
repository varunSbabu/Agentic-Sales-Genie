"""Classify node — determine the call type using the retrieved framework."""

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.llm import coerce_structured, get_llm, llm_available, missing_key_message
from backend.agent.state import CallTypeOutput, GenieState
from backend.utils.logging import logger

CLASSIFY_SYSTEM = """You are a sales-call type classifier.
Use ONLY the call type definitions in the RETRIEVED FRAMEWORK below.
If the framework does not define call types, choose one of:
Discovery | Demo | Technical | Commercial | Closing | Follow-up | Service | Other.

Be decisive — pick the SINGLE best label even if the call spans modes.
Use the dominant intent of the call, not the opening small talk.
"""

CLASSIFY_USER = """RETRIEVED FRAMEWORK:
{frameworks}

CALL METADATA:
- Platform: {platform}
- Duration: {duration} seconds
- Talk ratio (Rep/Prospect): {rep_ratio}% / {prospect_ratio}%
- Questions asked by Rep: {questions}

TRANSCRIPT (with speaker labels):
{transcript}

Classify the call. Return JSON only.
"""


async def classify_node(state: GenieState) -> dict:
    logger.info("classify: call_id={}", state.get("call_id"))
    if state.get("error"):
        return {"error": state.get("error")}
    if not llm_available(purpose="classify"):
        logger.warning("classify: {} — labeling as 'Other'", missing_key_message("classify"))
        return {"call_type": "Other"}

    try:
        llm = get_llm(temperature=0, max_tokens=512, purpose="classify").with_structured_output(CallTypeOutput)
        user_msg = CLASSIFY_USER.format(
            frameworks=state.get("retrieved_frameworks", ""),
            platform=state.get("platform", "unknown"),
            duration=state.get("duration_secs", 0),
            rep_ratio=state.get("talk_ratio_rep", 0),
            prospect_ratio=state.get("talk_ratio_prospect", 0),
            questions=state.get("question_count", 0),
            transcript=state.get("transcript_raw", "")[:6000],
        )
        raw = await llm.ainvoke(
            [SystemMessage(content=CLASSIFY_SYSTEM), HumanMessage(content=user_msg)]
        )
        result = coerce_structured(raw, CallTypeOutput)
        logger.info("classify: call_type={} ({})", result.call_type, result.reasoning)
        return {"call_type": result.call_type}
    except Exception as exc:  # noqa: BLE001
        logger.exception("classify failed: {}", exc)
        return {"call_type": "Other", "error": f"classify failed: {exc}"}
