"""LangGraph DAG for Sales Genie call analysis.

Topology (matches the spec exactly):

  START
    → preprocess
    → retrieve_kb
    → classify
    → score
    → coach
    → alert_decision (conditional)
        ├── "intervention" → build_intervention_alert
        ├── "coaching"     → build_coaching_alert
        └── "none"         → skip_alert
    → write_db
    → send_notification (conditional on alert_level)
    → END

LangSmith tracing is automatic when LANGCHAIN_TRACING_V2=true (set in .env).
Every node uses loguru for observability even when tracing is off.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.agent.nodes.alert import (
    build_coaching_alert_node,
    build_intervention_alert_node,
    skip_alert_node,
)
from backend.agent.nodes.classify import classify_node
from backend.agent.nodes.coach import coach_node
from backend.agent.nodes.dispatch import dispatch_connectors_node
from backend.agent.nodes.notify import send_notification_node, skip_notification_node
from backend.agent.nodes.preprocess import preprocess_node
from backend.agent.nodes.retrieve_kb import retrieve_kb_node
from backend.agent.nodes.router import decide_alert_level, should_send_notification
from backend.agent.nodes.score import score_node
from backend.agent.nodes.write_db import write_db_node
from backend.agent.state import GenieState
from backend.utils.logging import logger


def build_graph():
    """Compile the LangGraph DAG. Returns a runnable graph."""
    g: StateGraph[GenieState] = StateGraph(GenieState)

    # --- linear nodes (one-in, one-out) ------------------------------------
    g.add_node("preprocess", preprocess_node)
    g.add_node("retrieve_kb", retrieve_kb_node)
    g.add_node("classify", classify_node)
    g.add_node("score", score_node)
    g.add_node("coach", coach_node)

    # --- alert payload builders (siblings under the alert decision) --------
    g.add_node("build_intervention_alert", build_intervention_alert_node)
    g.add_node("build_coaching_alert", build_coaching_alert_node)
    g.add_node("skip_alert", skip_alert_node)

    # --- persistence + dispatch + delivery --------------------------------
    g.add_node("write_db", write_db_node)
    g.add_node("dispatch_connectors", dispatch_connectors_node)
    g.add_node("send_notification", send_notification_node)
    g.add_node("skip_notification", skip_notification_node)

    # --- edges -------------------------------------------------------------
    g.add_edge(START, "preprocess")
    g.add_edge("preprocess", "retrieve_kb")
    g.add_edge("retrieve_kb", "classify")
    g.add_edge("classify", "score")
    g.add_edge("score", "coach")

    # Conditional: alert level → one of three payload builders
    g.add_conditional_edges(
        "coach",
        decide_alert_level,
        {
            "intervention": "build_intervention_alert",
            "coaching": "build_coaching_alert",
            "none": "skip_alert",
        },
    )

    # All three converge on write_db
    g.add_edge("build_intervention_alert", "write_db")
    g.add_edge("build_coaching_alert", "write_db")
    g.add_edge("skip_alert", "write_db")

    # After Supabase row lands, fan out to Notion/Sheets in parallel.
    # Then decide whether to send alert notifications.
    g.add_edge("write_db", "dispatch_connectors")
    g.add_conditional_edges(
        "dispatch_connectors",
        should_send_notification,
        {"send": "send_notification", "skip": "skip_notification"},
    )

    g.add_edge("send_notification", END)
    g.add_edge("skip_notification", END)

    return g.compile()


# Compile once at import — the graph object is thread-safe and cheap to invoke.
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
        logger.info("LangGraph compiled — {} nodes", 11)
    return _graph


async def run_analysis(initial_state: GenieState) -> dict[str, Any]:
    """Execute the agent over an initial state. Returns the final state dict."""
    graph = get_graph()
    logger.info(
        "agent run starting: call_id={} user_id={} transcript_chars={}",
        initial_state.get("call_id"),
        initial_state.get("user_id"),
        len(initial_state.get("transcript_raw") or ""),
    )
    result = await graph.ainvoke(initial_state)
    logger.info(
        "agent run complete: score={:.2f} alert={} error={}",
        float(result.get("overall_score") or 0.0),
        result.get("alert_level", "none"),
        result.get("error"),
    )
    return result
