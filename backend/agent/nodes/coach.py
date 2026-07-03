"""Coach node — generate AI summary + call notes WITHOUT modifying scores.

This is a deliberately separated step from scoring. The prompt explicitly
forbids re-scoring or inventing evidence — coaching narratives must stay
faithful to the scoring node's output. This avoids LLMs subtly contradicting
their own scores when they get into 'helpful narrative' mode.
"""

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.llm import coerce_structured, get_llm, llm_available, missing_key_message
from backend.agent.state import CoachOutput, GenieState
from backend.utils.logging import logger

COACH_SYSTEM = """You are a READ-ONLY coaching narrative generator.
You MUST NOT re-score any dimension.
You MUST NOT modify any numeric value.
You MUST NOT invent new evidence.
Use ONLY the data provided below.

Generate AI_Summary: 4-6 sentences structured as:
1. Call type context
2. Rep strengths (from provided strengths only)
3. Buyer signals (from provided evidence only)
4. Gaps and missed opportunities (from improvements only)
5. Deal progression: explicitly state Advanced/Stalled/Created Risk
6. One specific coaching recommendation

Generate Genie_Call_Notes: 1-2 factual sentences only.
What happened. Current status. Next step.
No coaching. No interpretation. Strictly factual.
"""

COACH_USER = """INPUT DATA:
Call Type: {call_type}
Overall Score: {overall_score} / 5.0 ({score_band})
Strengths: {strengths}
Improvements: {improvements}
Next Step Quality: {next_step_quality}
Loss Risk Categories: {loss_risk_categories}
Talk Ratio — Rep: {talk_ratio_rep}% / Prospect: {talk_ratio_prospect}%

Return JSON:
{{"ai_summary": "...", "call_notes": "..."}}
"""


async def coach_node(state: GenieState) -> dict:
    logger.info("coach: call_id={}", state.get("call_id"))
    if state.get("error"):
        return {"error": state.get("error")}
    if not llm_available(purpose="coach"):
        return {
            "ai_summary": f"[{missing_key_message('coach')} — narrative skipped]",
            "call_notes": "Analysis incomplete.",
        }

    try:
        llm = get_llm(temperature=0, max_tokens=1024, purpose="coach").with_structured_output(CoachOutput)
        user_msg = COACH_USER.format(
            call_type=state.get("call_type", "Other"),
            overall_score=state.get("overall_score", 0.0),
            score_band=state.get("score_band", ""),
            strengths=state.get("strengths", []),
            improvements=state.get("improvements", []),
            next_step_quality=state.get("next_step_quality", ""),
            loss_risk_categories=state.get("loss_risk_categories", []),
            talk_ratio_rep=state.get("talk_ratio_rep", 0),
            talk_ratio_prospect=state.get("talk_ratio_prospect", 0),
        )
        raw = await llm.ainvoke(
            [SystemMessage(content=COACH_SYSTEM), HumanMessage(content=user_msg)]
        )
        result = coerce_structured(raw, CoachOutput)
        logger.info("coach: ai_summary={} chars, call_notes={} chars",
                    len(result.ai_summary), len(result.call_notes))
        return {"ai_summary": result.ai_summary, "call_notes": result.call_notes}
    except Exception as exc:  # noqa: BLE001
        logger.exception("coach failed: {}", exc)
        return {
            "ai_summary": "[coach node failed]",
            "call_notes": "Analysis available; narrative generation failed.",
            "error": f"coach failed: {exc}",
        }
