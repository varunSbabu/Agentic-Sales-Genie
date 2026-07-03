"""Conditional routing functions for the LangGraph DAG.

These functions are NOT nodes themselves — they're pure decision functions
used by LangGraph's `add_conditional_edges()` to dispatch state to one of
several downstream nodes.
"""

from backend.agent.nodes.alert import decide_alert_level  # noqa: F401 re-export
from backend.agent.state import GenieState


def should_send_notification(state: GenieState) -> str:
    """Return 'send' if a notification is warranted, else 'skip'."""
    if state.get("error"):
        return "skip"
    level = state.get("alert_level", "none")
    if level in ("intervention", "coaching"):
        return "send"
    return "skip"


def has_fatal_error(state: GenieState) -> str:
    """Used to short-circuit the graph if preprocess detects bad input."""
    return "abort" if state.get("error") else "continue"
