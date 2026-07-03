"""Score node — strictly applies the retrieved framework to score the call.

The prompt is taken VERBATIM from the spec and is the most important constraint
in the whole system. Key rules baked into the system message:
  - Apply ONLY the retrieved framework, never generic sales knowledge
  - Do not invent criteria not present in the framework
  - Every score must be evidence-based with transcript quotes
  - Score conservatively when evidence is missing
"""

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.llm import coerce_structured, get_llm, llm_available, missing_key_message
from backend.agent.state import AnalysisOutput, GenieState
from backend.utils.logging import logger

SCORE_SYSTEM = """You are a sales call evaluator.
Apply ONLY the scoring frameworks retrieved below.
Do NOT use generic sales knowledge.
Do NOT invent criteria not present in the frameworks.
Return ONLY valid JSON matching the exact schema provided.
Every score must be evidence-based from the transcript.
If evidence is missing for a dimension, score conservatively.

CRITICAL — STRICT GROUNDING:
The retrieved framework includes example PHRASES (such as "manual CSV pulls",
"automation pitch", "in revenue terms", "we lose maybe 20% of new users").
These describe HYPOTHETICAL DIFFERENT CALLS. You MUST NOT copy phrasing
from those examples into your strengths, improvements, or reasoning. Every
quote in the `evidence`, `objections`, `buying_signals`, `competitors_mentioned`,
or `key_quotes` fields must appear VERBATIM in the TRANSCRIPT below.

Strengths and improvements must reference what THIS rep did or didn't do
in THIS specific transcript — not what a hypothetical rep in the framework
examples did.

OUTPUT REQUIREMENTS:
  - call_title: a 4-8 word title summarizing the call topic. Concrete and
    descriptive. Examples: "Nissan Map Update Order", "Acme Corp Discovery",
    "Contract Redlining — Vendor X". NOT generic like "Sales Call" or "Demo".
  - prospect_name: the person or company on the OTHER side of the call.
    Prefer a person name if introduced ("John Smith"); fall back to a company
    if only the company was mentioned. Empty string only if neither was said
    in the transcript.
  - call_type_justification: ONE sentence stating the dominant intent that drove the label.
  - score_justification: 2–3 sentences explaining WHY the overall score, anchored to dimensions.
  - dimension_scores: MUST contain ALL dimensions from the framework (don't skip any).
  - objections: list every objection the prospect raised. For each: verbatim quote, short
    category (PRICE / TIMING / COMPETITOR / AUTHORITY / NEED), whether the rep addressed it,
    and one sentence on how. If none, return empty list.
  - buying_signals: positive intent signals — urgency, agreement, budget confirmed, pain admitted.
    For each: verbatim quote, category, strength (weak/medium/strong). Empty list if none.
  - competitors_mentioned: any competitor named. For each: name, context quote, sentiment.
    Empty list if none.
  - next_step_action: the concrete next-step action agreed (e.g. "Send case studies by Friday and
    schedule demo Tuesday 2pm"). Empty string if no next step set.
  - next_step_owner: who owns the action (Rep | Prospect | Both | a named person).
  - call_summary_bullets: 3–5 factual one-line bullets summarising the call.
  - key_quotes: 2–4 most notable verbatim transcript moments. For each: quote, speaker, why notable.
"""

SCORE_USER = """RETRIEVED FRAMEWORKS:
{retrieved_frameworks}

CALL METADATA:
Call Type: {call_type}
Duration: {duration_secs} seconds
Talk Ratio — Rep: {talk_ratio_rep}% / Prospect: {talk_ratio_prospect}%
Questions Asked by Rep: {question_count}
Platform: {platform}

TRANSCRIPT (with speaker labels):
{formatted_transcript}

SCORING INSTRUCTION:
Score each dimension defined in the frameworks above.
Extract direct evidence quotes from the transcript.
Be strict. A conversationally strong call may score low
if discovery health or selling discipline is weak.

Return JSON matching the AnalysisOutput schema exactly.
"""


async def score_node(state: GenieState) -> dict:
    logger.info(
        "score: call_id={} call_type={}",
        state.get("call_id"),
        state.get("call_type"),
    )
    if state.get("error"):
        return {"error": state.get("error")}
    if not llm_available(purpose="score"):
        return {"error": f"{missing_key_message('score')} — cannot score the call"}

    try:
        llm = get_llm(temperature=0, max_tokens=4096, purpose="score").with_structured_output(AnalysisOutput)
        user_msg = SCORE_USER.format(
            retrieved_frameworks=state.get("retrieved_frameworks", ""),
            call_type=state.get("call_type", "Other"),
            duration_secs=state.get("duration_secs", 0),
            talk_ratio_rep=state.get("talk_ratio_rep", 0),
            talk_ratio_prospect=state.get("talk_ratio_prospect", 0),
            question_count=state.get("question_count", 0),
            platform=state.get("platform", "unknown"),
            formatted_transcript=state.get("transcript_raw", ""),
        )
        raw = await llm.ainvoke(
            [SystemMessage(content=SCORE_SYSTEM), HumanMessage(content=user_msg)]
        )
        result = coerce_structured(raw, AnalysisOutput)
        logger.info(
            "score: overall={:.2f} band={} dims={}",
            result.overall_score,
            result.score_band,
            len(result.dimension_scores),
        )

        return {
            # meeting identity
            "call_title": result.call_title or "",
            "prospect_name": result.prospect_name or "",
            # core classification + scoring
            "call_type": result.call_type or state.get("call_type"),
            "call_type_justification": result.call_type_justification or "",
            "methodology_id": result.methodology_id or "GENIE_v1",
            "overall_score": float(result.overall_score),
            "score_band": result.score_band,
            "score_justification": result.score_justification or "",
            "dimension_scores": [d.model_dump() for d in result.dimension_scores],
            "strengths": list(result.strengths),
            "improvements": list(result.improvements),
            # structured signals
            "objections": [o.model_dump() for o in result.objections],
            "buying_signals": [b.model_dump() for b in result.buying_signals],
            "competitors_mentioned": [c.model_dump() for c in result.competitors_mentioned],
            # risk + next step
            "next_step_quality": result.next_step_quality,
            "next_step_action": result.next_step_action or "",
            "next_step_owner": result.next_step_owner or "",
            "loss_risk_categories": list(result.loss_risk_categories),
            # skimmable summaries
            "call_summary_bullets": list(result.call_summary_bullets),
            "key_quotes": [k.model_dump() for k in result.key_quotes],
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("score failed: {}", exc)
        return {"error": f"score failed: {exc}"}
