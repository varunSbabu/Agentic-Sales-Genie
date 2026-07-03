"""Shared pytest fixtures.

Design goals:
  - The suite runs GREEN without any external API keys (LLM is mocked; DB /
    network-dependent tests skip cleanly when unavailable).
  - Real local components (ChromaDB, sentence-transformers, the LangGraph DAG)
    are exercised for real — they don't need the network.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Session-scoped event loop.
# The app's async SQLAlchemy engine is a module global; asyncpg binds its pool
# connections to the loop that created them. pytest-asyncio's default is a
# fresh loop per test, which makes the shared engine fail on the 2nd+ DB test
# (and manifests as flaky "db not reachable" skips). One loop for the whole
# session keeps the engine's connections valid across every test.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# DB availability guard — skip DB-backed tests if Supabase can't be reached
# ---------------------------------------------------------------------------
async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text
        from backend.db.session import async_engine
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest_asyncio.fixture
async def db_up():
    if not await _db_reachable():
        pytest.skip("database not reachable — skipping DB-backed test")
    return True


# ---------------------------------------------------------------------------
# HTTP client bound to the FastAPI app (no live server needed)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client():
    import httpx
    from backend.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Fake LLM — lets us test the agent's happy path without a real provider
# ---------------------------------------------------------------------------
class _FakeStructured:
    """Mimics `llm.with_structured_output(Schema)` → `.ainvoke(msgs)`."""

    def __init__(self, schema, payload):
        self._schema = schema
        self._payload = payload

    async def ainvoke(self, messages):
        return self._schema(**self._payload)


class _FakeLLM:
    def __init__(self, payload_for):
        # payload_for: dict mapping schema-name -> payload dict
        self._payload_for = payload_for

    def with_structured_output(self, schema):
        payload = self._payload_for.get(schema.__name__, {})
        return _FakeStructured(schema, payload)


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch get_llm + llm_available across all agent nodes so classify/score/
    coach run deterministically offline."""
    from backend.agent.state import AnalysisOutput, CallTypeOutput, CoachOutput

    payloads = {
        "CallTypeOutput": {"call_type": "Discovery", "reasoning": "test"},
        "AnalysisOutput": {
            "call_title": "Test Call",
            "prospect_name": "Test Prospect",
            "call_type": "Discovery",
            "call_type_justification": "explored needs",
            "methodology_id": "GENIE_v1",
            "overall_score": 3.6,
            "score_band": "SOLID",
            "score_justification": "solid discovery with a clear next step",
            "dimension_scores": [
                {"dimension": "Discovery Quality", "score": 4.0, "max_score": 5.0,
                 "evidence": ["Rep: what is your biggest pain?"], "reasoning": "good probing"},
                {"dimension": "Next Step Clarity", "score": 4.0, "max_score": 5.0,
                 "evidence": ["Rep: Thursday 2pm?"], "reasoning": "specific"},
            ],
            "strengths": ["Quantified the pain"],
            "improvements": ["Could confirm budget"],
            "objections": [],
            "buying_signals": [{"quote": "we need this by Q3", "category": "URGENCY", "strength": "strong"}],
            "competitors_mentioned": [],
            "next_step_quality": "ADVANCED",
            "next_step_action": "Demo Thursday 2pm",
            "next_step_owner": "Rep",
            "loss_risk_categories": [],
            "call_summary_bullets": ["Discovery call", "Pain quantified"],
            "key_quotes": [{"quote": "we lose $170k/mo", "speaker": "Prospect", "why_notable": "quantified pain"}],
        },
        "CoachOutput": {
            "ai_summary": "A solid discovery call with quantified pain and a booked demo.",
            "call_notes": "Rep ran discovery and booked a demo for Thursday.",
        },
    }
    fake = _FakeLLM(payloads)

    import backend.agent.nodes.classify as classify_mod
    import backend.agent.nodes.score as score_mod
    import backend.agent.nodes.coach as coach_mod

    for mod in (classify_mod, score_mod, coach_mod):
        monkeypatch.setattr(mod, "llm_available", lambda purpose=None: True)
        monkeypatch.setattr(mod, "get_llm", lambda **kw: fake)

    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def unique_email() -> str:
    return f"pytest_{uuid.uuid4().hex[:12]}@example.com"
