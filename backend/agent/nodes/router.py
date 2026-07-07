"""Conditional routing functions for the LangGraph DAG.

These functions are NOT nodes themselves — they're pure decision functions
used by LangGraph's `add_conditional_edges()` to dispatch state to one of
several downstream nodes.
"""

from backend.agent.nodes.alert import decide_alert_level  # noqa: F401 re-export
from backend.agent.state import GenieState


def should_send_notification(state: GenieState) -> str:
    """Route to the notification node for EVERY successful analysis.

    A professional call-analysis overview email is sent for every call; the
    intervention/coaching variants are just different banners on the same
    email. The notify node itself honours the user's notify_email preference.
    Only skip if the analysis failed or never persisted.
    """
    if state.get("error"):
        return "skip"
    if not state.get("analysis_id"):
        return "skip"
    return "send"


def has_fatal_error(state: GenieState) -> str:
    """Used to short-circuit the graph if preprocess detects bad input."""
    return "abort" if state.get("error") else "continue"
