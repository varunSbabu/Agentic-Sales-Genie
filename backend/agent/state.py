"""LangGraph state container + Pydantic output schemas for Sales Genie.

The spec is explicit about field shape. We keep this 1:1 with the spec so
downstream consumers (Phase 6 connectors, Phase 7 notifications) don't need
adapters.
"""

from typing import TypedDict, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic schemas — what the LLM returns, validated by `with_structured_output`
# ---------------------------------------------------------------------------
class DimensionScore(BaseModel):
    """A single scored rubric dimension with evidence."""

    dimension: str = Field(default="", description="Name of the scoring dimension from the framework.")
    score: float = Field(default=0.0, ge=0, le=5, description="Numeric score for this dimension.")
    max_score: float = Field(default=5.0, description="Max possible (typically 5).")
    evidence: list[str] = Field(
        default_factory=list,
        description="Direct quotes from the transcript supporting the score.",
    )
    reasoning: str = Field(
        default="",
        description="Why this score given the framework rubric and evidence.",
    )


class Objection(BaseModel):
    quote: str = Field(default="", description="Verbatim prospect quote where the objection was raised.")
    category: str = Field(default="OTHER", description="Short category label e.g. PRICE, TIMING, COMPETITOR, AUTHORITY, NEED.")
    was_addressed: bool = Field(default=False, description="Did the rep explicitly handle this objection?")
    how_handled: str = Field(default="", description="One sentence on how the rep handled it (or didn't).")


class BuyingSignal(BaseModel):
    quote: str = Field(default="", description="Verbatim prospect quote demonstrating intent or interest.")
    category: str = Field(default="OTHER", description="e.g. URGENCY, BUDGET_CONFIRMED, AUTHORITY_REVEALED, PAIN_ADMITTED.")
    strength: str = Field(default="medium", description="weak | medium | strong")


class CompetitorMention(BaseModel):
    name: str = Field(default="", description="Competitor name as mentioned in the transcript.")
    context_quote: str = Field(default="", description="Verbatim quote where the competitor was mentioned.")
    sentiment: str = Field(default="neutral", description="negative | neutral | positive — how the prospect framed them.")


class KeyQuote(BaseModel):
    quote: str = Field(default="", description="Verbatim quote from the transcript.")
    speaker: str = Field(default="Rep", description="Rep | Prospect")
    why_notable: str = Field(default="", description="One short reason this quote matters for coaching.")


class AnalysisOutput(BaseModel):
    """Full LLM-produced scoring object. Returned by the score node."""

    # --- Meeting identity (used by alert emails + UI) --------------------
    call_title: str = Field(
        default="",
        description=(
            "A 4-8 word title summarizing the call topic, e.g. "
            "'Nissan Map Update Order' or 'Acme Corp Discovery Call'. "
            "Concrete and descriptive — not generic like 'Sales Call'."
        ),
    )
    prospect_name: str = Field(
        default="",
        description=(
            "The prospect / customer name extracted from the transcript. "
            "Prefer a person name if introduced (e.g. 'John Smith'); fall back "
            "to a company name if only the company was mentioned. Empty string "
            "if neither was disclosed on the call."
        ),
    )

    # --- Classification --------------------------------------------------
    # Most string fields have defaults so a Llama model that omits one doesn't
    # fail the entire tool-call validation. The prompt still requests them all.
    call_type: str = Field(default="Other", description="Classified call type (Discovery, Demo, Commercial, Service, etc.).")
    call_type_justification: str = Field(
        default="",
        description="One sentence: why this call type label, citing the dominant intent observed.",
    )
    methodology_id: str = Field(
        default="GENIE_v1",
        description="The framework version used to score (default GENIE_v1).",
    )

    # --- Scoring ---------------------------------------------------------
    overall_score: float = Field(default=0.0, ge=0, le=5, description="Weighted overall 0–5.")
    score_band: str = Field(default="", description="EXCELLENT | SOLID | MIXED | INTERVENTION REQUIRED.")
    score_justification: str = Field(
        default="",
        description="2–3 sentence explanation of WHY this overall score given the dimensions.",
    )
    dimension_scores: list[DimensionScore] = Field(
        default_factory=list, description="Per-dimension scoring details. Must contain ALL framework dimensions.",
    )

    # --- Coaching content ------------------------------------------------
    strengths: list[str] = Field(
        default_factory=list,
        description="3–5 specific strengths anchored to this transcript (no copying framework examples).",
    )
    improvements: list[str] = Field(
        default_factory=list,
        description="3–5 specific improvements anchored to this transcript (no copying framework examples).",
    )

    # --- Signals + objections + competitors (NEW) ------------------------
    objections: list[Objection] = Field(
        default_factory=list,
        description="Every objection the prospect raised in the transcript, with handling status.",
    )
    buying_signals: list[BuyingSignal] = Field(
        default_factory=list,
        description="Positive intent signals from the prospect (urgency, agreement, budget confirmed, etc.).",
    )
    competitors_mentioned: list[CompetitorMention] = Field(
        default_factory=list,
        description="Any competitor named in the transcript. Empty list if none.",
    )

    # --- Risk + next step ------------------------------------------------
    next_step_quality: str = Field(
        default="",
        description="ADVANCED | STALLED | CREATED_RISK label.",
    )
    next_step_action: str = Field(
        default="",
        description="Concrete next-step action agreed in the call. Empty if none.",
    )
    next_step_owner: str = Field(
        default="",
        description="Who owns the next-step action (Rep | Prospect | Both | a name). Empty if none.",
    )
    loss_risk_categories: list[str] = Field(
        default_factory=list,
        description="Loss-risk flags from the framework (NO_BUDGET_CONFIRMED, VAGUE_PAIN, etc.).",
    )

    # --- Skimmable summaries (NEW) ---------------------------------------
    call_summary_bullets: list[str] = Field(
        default_factory=list,
        description="3–5 factual one-line bullets summarising what happened on the call.",
    )
    # Plain strings, not objects: Llama tool-calling reliably emits quotes as a
    # flat string array and Groq rejects the whole tool call (400 tool_use_failed)
    # when the schema demands nested objects here. Strings are all the UI needs.
    key_quotes: list[str] = Field(
        default_factory=list,
        description="2–4 most notable verbatim quote strings from the call.",
    )


class CoachOutput(BaseModel):
    """Narrative coaching output. Generated by the coach node, never re-scores."""

    ai_summary: str = Field(
        description="4–6 sentence call summary structured per the prompt."
    )
    call_notes: str = Field(
        description="1–2 sentence factual call note. No coaching, no interpretation."
    )


class CallTypeOutput(BaseModel):
    """Result of the classify node — just the call type label + reasoning."""

    call_type: str = Field(description="One of: Discovery, Demo, Technical, Commercial, Closing, Follow-up, Other.")
    reasoning: str = Field(description="One-sentence why.")


# ---------------------------------------------------------------------------
# Graph state — the dict passed between LangGraph nodes
# ---------------------------------------------------------------------------
class GenieState(TypedDict, total=False):
    # Identity ---------------------------------------------------------------
    user_id: str
    call_id: str

    # Raw input --------------------------------------------------------------
    transcript_raw: str
    transcript_speakers: list[dict]
    talk_ratio_rep: float
    talk_ratio_prospect: float
    question_count: int
    platform: str
    duration_secs: int

    # KB context -------------------------------------------------------------
    kb_collection: str
    retrieved_frameworks: str

    # Agent outputs — meeting identity (NEW) --------------------------------
    call_title: str
    prospect_name: str

    # Agent outputs — core --------------------------------------------------
    call_type: str
    call_type_justification: str
    methodology_id: str
    dimension_scores: list[dict]
    evidence: dict
    strengths: list[str]
    improvements: list[str]
    overall_score: float
    score_band: str
    score_justification: str

    # Agent outputs — structured (NEW) --------------------------------------
    objections: list[dict]
    buying_signals: list[dict]
    competitors_mentioned: list[dict]
    call_summary_bullets: list[str]
    key_quotes: list[str]

    # Agent outputs — risk + next step --------------------------------------
    next_step_quality: str
    next_step_action: str
    next_step_owner: str
    loss_risk_categories: list[str]

    # Mode B (coach narrative) ----------------------------------------------
    ai_summary: str
    call_notes: str

    # Mode C (alerts) -------------------------------------------------------
    alert_level: str
    alert_email_html: str
    alert_email_subject: str
    alert_slack_message: dict

    # Action tracking --------------------------------------------------------
    crm_written: bool
    notification_sent: bool
    analysis_id: str
    error: Optional[str]
    extras_connector_results: list[dict]  # one entry per connector that fired

    # User preferences (loaded at entry) ------------------------------------
    alert_threshold_low: float
    alert_threshold_high: float
    notify_email: bool
    notify_slack: bool
    manager_email: Optional[str]
