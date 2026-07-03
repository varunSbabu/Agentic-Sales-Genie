"""Agent tests — nodes, graph structure, schema validation, alert routing.

The LLM is mocked (fake_llm fixture) so classify/score/coach run offline.
"""

import pytest

from backend.agent.state import AnalysisOutput, CoachOutput, DimensionScore, GenieState


# ---------------------------------------------------------------------------
# Pydantic schema validation
# ---------------------------------------------------------------------------
def test_analysis_output_validates():
    out = AnalysisOutput(
        overall_score=3.5,
        score_band="SOLID",
        next_step_quality="ADVANCED",
        dimension_scores=[DimensionScore(dimension="Discovery", score=4, reasoning="x")],
    )
    assert out.overall_score == 3.5
    assert out.dimension_scores[0].max_score == 5.0  # default


def test_dimension_score_bounds():
    with pytest.raises(Exception):
        DimensionScore(dimension="X", score=9.0, reasoning="too high")  # >5 rejected


# ---------------------------------------------------------------------------
# Graph structure — matches the spec topology
# ---------------------------------------------------------------------------
def test_graph_compiles_with_expected_nodes():
    from backend.agent.graph import build_graph
    g = build_graph()
    nodes = set(g.get_graph().nodes.keys())
    for expected in [
        "preprocess", "retrieve_kb", "classify", "score", "coach",
        "build_intervention_alert", "build_coaching_alert", "skip_alert",
        "write_db", "dispatch_connectors", "send_notification", "skip_notification",
    ]:
        assert expected in nodes, f"missing node {expected}"


def test_graph_has_alert_branches():
    from backend.agent.graph import build_graph
    g = build_graph()
    edges = [(e.source, e.target) for e in g.get_graph().edges]
    # coach fans out to the three alert builders
    targets_from_coach = {t for s, t in edges if s == "coach"}
    assert {"build_intervention_alert", "build_coaching_alert", "skip_alert"} <= targets_from_coach
    # write_db → dispatch_connectors → notification
    assert ("write_db", "dispatch_connectors") in edges


# ---------------------------------------------------------------------------
# preprocess node — pure logic, no external deps
# ---------------------------------------------------------------------------
async def test_preprocess_rejects_empty_transcript():
    from backend.agent.nodes.preprocess import preprocess_node
    out = await preprocess_node({"user_id": "u1", "call_id": "c1", "transcript_raw": "   "})
    assert out.get("error")


async def test_preprocess_sets_collection():
    from backend.agent.nodes.preprocess import preprocess_node
    out = await preprocess_node({"user_id": "abc", "call_id": "c1", "transcript_raw": "Rep: hi"})
    assert out["kb_collection"] == "user_abc"
    assert out["crm_written"] is False
    assert out["error"] is None


# ---------------------------------------------------------------------------
# alert routing — pure decision function
# ---------------------------------------------------------------------------
def test_decide_alert_level():
    from backend.agent.nodes.alert import decide_alert_level
    base = {"alert_threshold_low": 2.5, "alert_threshold_high": 4.0}
    assert decide_alert_level({**base, "overall_score": 1.8}) == "intervention"
    assert decide_alert_level({**base, "overall_score": 4.5}) == "coaching"
    assert decide_alert_level({**base, "overall_score": 3.2}) == "none"


def test_decide_alert_level_guards_zero_and_error():
    from backend.agent.nodes.alert import decide_alert_level
    # score 0 (agent failed to score) must NOT trigger intervention
    assert decide_alert_level({"overall_score": 0.0}) == "none"
    assert decide_alert_level({"overall_score": 1.0, "error": "boom"}) == "none"


# ---------------------------------------------------------------------------
# classify / score / coach nodes with the fake LLM
# ---------------------------------------------------------------------------
async def test_classify_node(fake_llm):
    from backend.agent.nodes.classify import classify_node
    out = await classify_node({"call_id": "c1", "retrieved_frameworks": "fw", "transcript_raw": "Rep: hi"})
    assert out["call_type"] == "Discovery"


async def test_score_node_returns_valid_shape(fake_llm):
    from backend.agent.nodes.score import score_node
    out = await score_node({
        "call_id": "c1", "retrieved_frameworks": "fw",
        "transcript_raw": "Rep: hi\nProspect: hello",
        "call_type": "Discovery",
    })
    assert out["overall_score"] == 3.6
    assert out["score_band"] == "SOLID"
    assert len(out["dimension_scores"]) == 2
    assert out["buying_signals"][0]["category"] == "URGENCY"
    assert out["next_step_owner"] == "Rep"


async def test_coach_does_not_rescore(fake_llm):
    """The coach node must never emit numeric score fields — it only narrates."""
    from backend.agent.nodes.coach import coach_node
    out = await coach_node({
        "call_id": "c1", "call_type": "Discovery",
        "overall_score": 3.6, "score_band": "SOLID",
        "strengths": ["x"], "improvements": ["y"],
        "next_step_quality": "ADVANCED", "loss_risk_categories": [],
        "talk_ratio_rep": 40, "talk_ratio_prospect": 60,
    })
    assert "ai_summary" in out and "call_notes" in out
    for forbidden in ("overall_score", "score_band", "dimension_scores"):
        assert forbidden not in out, f"coach must not modify {forbidden}"


async def test_score_node_no_llm_degrades_gracefully(monkeypatch):
    """Without a provider key, score_node returns a structured error, not a crash."""
    import backend.agent.nodes.score as score_mod
    monkeypatch.setattr(score_mod, "llm_available", lambda purpose=None: False)
    out = await score_mod.score_node({"call_id": "c1", "transcript_raw": "Rep: hi"})
    assert out.get("error")
