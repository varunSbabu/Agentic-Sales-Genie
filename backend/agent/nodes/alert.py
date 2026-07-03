"""Alert decision + payload builders.

The alert_decision_node is the only conditional router in the graph. It
returns a string ('intervention' | 'coaching' | 'none') that LangGraph uses
to dispatch to one of three sibling nodes. Each sibling sets fields like
`alert_email_subject` etc. Phase 7 will populate the actual email/Slack
content; here we set placeholders so the contract is stable.
"""

from typing import Literal

from backend.agent.state import GenieState
from backend.utils.logging import logger

AlertLevel = Literal["intervention", "coaching", "none"]


# ---------------------------------------------------------------------------
# Decision (used as a LangGraph conditional edge function)
# ---------------------------------------------------------------------------
def decide_alert_level(state: GenieState) -> AlertLevel:
    """Pure function: compare overall_score to user thresholds.

    When the agent couldn't score (e.g., no Anthropic key), `overall_score`
    will be 0.0, which is below any reasonable threshold and would route to
    'intervention' — misleading. So we short-circuit to 'none' if state
    carries an error or never produced a non-zero score.
    """
    if state.get("error"):
        return "none"
    raw = state.get("overall_score")
    if raw is None or float(raw) <= 0.0:
        return "none"

    score = float(raw)
    low = float(state.get("alert_threshold_low") or 2.5)
    high = float(state.get("alert_threshold_high") or 4.0)

    if score < low:
        return "intervention"
    if score >= high:
        return "coaching"
    return "none"


# ---------------------------------------------------------------------------
# Sibling nodes — each fills the alert payload placeholders
# ---------------------------------------------------------------------------
def _score(state: GenieState) -> float:
    """Safe extractor — LangGraph initialises TypedDict fields to None, so
    `state.get("k", default)` returns None (not default). Use this helper
    everywhere we need a numeric value."""
    return float(state.get("overall_score") or 0.0)


def _threshold_low(state: GenieState) -> float:
    return float(state.get("alert_threshold_low") or 2.5)


def _threshold_high(state: GenieState) -> float:
    return float(state.get("alert_threshold_high") or 4.0)


async def build_intervention_alert_node(state: GenieState) -> dict:
    logger.info(
        "alert: INTERVENTION (score={:.2f} < threshold_low={:.2f})",
        _score(state),
        _threshold_low(state),
    )
    return {
        "alert_level": "intervention",
        "alert_email_subject": (
            f"🚨 Intervention Required — score {_score(state):.1f}/5.0"
        ),
        "alert_email_html": "",  # Phase 7 will populate via templates
        "alert_slack_message": {},
    }


async def build_coaching_alert_node(state: GenieState) -> dict:
    logger.info(
        "alert: COACHING (score={:.2f} >= threshold_high={:.2f})",
        _score(state),
        _threshold_high(state),
    )
    return {
        "alert_level": "coaching",
        "alert_email_subject": (
            f"⭐ Coaching Example — score {_score(state):.1f}/5.0"
        ),
        "alert_email_html": "",
        "alert_slack_message": {},
    }


async def skip_alert_node(state: GenieState) -> dict:
    logger.info(
        "alert: none (score={:.2f} between thresholds)", _score(state)
    )
    return {"alert_level": "none"}
