"""API tests — auth flow, unauthenticated rejection, KB status, job polling.

Uses httpx over the ASGI app (no live server). DB-backed tests skip if the
database is unreachable.
"""

import pytest

from tests.conftest import unique_email


# ---------------------------------------------------------------------------
# Unauthenticated access is rejected (no DB needed)
# ---------------------------------------------------------------------------
async def test_health_open(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("path", [
    "/auth/me", "/config/kb/status", "/config/integrations",
    "/config/preferences", "/history/stats", "/analysis/history",
])
async def test_protected_endpoints_reject_anonymous(client, path):
    r = await client.get(path)
    assert r.status_code == 401, f"{path} should require auth, got {r.status_code}"


# ---------------------------------------------------------------------------
# Full auth flow (needs DB)
# ---------------------------------------------------------------------------
async def test_register_login_me_flow(client, db_up):
    email = unique_email()
    password = "Password1234"

    # register
    r = await client.post("/auth/register", json={"email": email, "password": password, "full_name": "PyTest"})
    assert r.status_code == 201, r.text
    tokens = r.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    # duplicate register → 409
    r = await client.post("/auth/register", json={"email": email, "password": password, "full_name": "PyTest"})
    assert r.status_code == 409

    # login
    r = await client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    access = r.json()["access_token"]

    # wrong password → 401
    r = await client.post("/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert r.status_code == 401

    # /me with token
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["email"] == email
    assert "hashed_password" not in r.json()  # never leak the hash


async def test_weak_password_rejected(client, db_up):
    r = await client.post("/auth/register", json={
        "email": unique_email(), "password": "short", "full_name": "X"})
    assert r.status_code == 422  # pydantic min_length / strength validator


# ---------------------------------------------------------------------------
# KB status + preferences for a fresh user (needs DB)
# ---------------------------------------------------------------------------
async def test_new_user_kb_and_prefs(client, db_up):
    email = unique_email()
    reg = await client.post("/auth/register", json={"email": email, "password": "Password1234", "full_name": "Kb"})
    token = reg.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # fresh KB is empty
    r = await client.get("/config/kb/status", headers=h)
    assert r.status_code == 200
    assert r.json()["total_chunks"] == 0

    # default preferences
    r = await client.get("/config/preferences", headers=h)
    assert r.status_code == 200
    prefs = r.json()
    assert prefs["alert_threshold_low"] == 2.5
    assert prefs["alert_threshold_high"] == 4.0

    # update preferences
    r = await client.put("/config/preferences", headers=h, json={
        "alert_threshold_low": 2.0, "alert_threshold_high": 4.2, "manager_email": "boss@x.com"})
    assert r.status_code == 200
    assert r.json()["manager_email"] == "boss@x.com"

    # invalid: low >= high
    r = await client.put("/config/preferences", headers=h, json={
        "alert_threshold_low": 4.5, "alert_threshold_high": 4.0})
    assert r.status_code == 400


async def test_job_poll_unknown_id(client, db_up):
    email = unique_email()
    reg = await client.post("/auth/register", json={"email": email, "password": "Password1234", "full_name": "J"})
    token = reg.json()["access_token"]
    r = await client.get("/analysis/job/nonexistent-job-id", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
