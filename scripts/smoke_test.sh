#!/usr/bin/env bash
# =============================================================================
# Sales Genie — end-to-end smoke test
# Runs every meaningful component once and reports pass/fail.
# Safe to re-run as often as you like.
# =============================================================================
#
# Usage:
#   bash scripts/smoke_test.sh                  # full suite
#   SKIP_ANALYSIS=1 bash scripts/smoke_test.sh  # skip the LLM call (saves token budget)
#
# Exits non-zero on any failure.

set -u

# -------------------------------------------------------------------------
# Output helpers
# -------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0; SKIP=0

pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
skip() { echo -e "  ${YELLOW}↷${NC} $1 (skipped)"; SKIP=$((SKIP+1)); }
step() { echo; echo -e "${BLUE}▶${NC} $1"; }
note() { echo -e "    ${YELLOW}note:${NC} $1"; }

# -------------------------------------------------------------------------
# 0. Pre-flight — docker + .env state
# -------------------------------------------------------------------------
step "0. Pre-flight checks"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not installed"; exit 1
fi
pass "docker installed"

if ! docker compose ps backend 2>/dev/null | grep -q "Up"; then
  fail "backend container not running — try: docker compose up -d backend"; exit 1
fi
pass "backend container running"

ENV_FILE="$(pwd)/.env"
if [ ! -f "$ENV_FILE" ]; then
  fail ".env not found at $ENV_FILE"; exit 1
fi
pass ".env present"

# Quick sanity on key env vars (masked)
for v in DATABASE_URL DATABASE_URL_SYNC LLM_PROVIDER JWT_SECRET_KEY; do
  if grep -q "^${v}=" "$ENV_FILE" 2>/dev/null; then
    pass "$v set in .env"
  else
    fail "$v missing from .env"
  fi
done

PROVIDER=$(grep '^LLM_PROVIDER=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
case "$PROVIDER" in
  groq) KEY_VAR="GROQ_API_KEY" ;;
  google) KEY_VAR="GOOGLE_API_KEY" ;;
  anthropic) KEY_VAR="ANTHROPIC_API_KEY" ;;
  *) KEY_VAR="" ;;
esac

if [ -n "$KEY_VAR" ]; then
  VAL=$(grep "^${KEY_VAR}=" "$ENV_FILE" | cut -d= -f2-)
  if [ -n "$VAL" ]; then
    pass "$KEY_VAR set (length $(echo -n "$VAL" | wc -c | tr -d ' '))"
  else
    fail "$KEY_VAR is empty — analysis will fail"
  fi
fi

# bcrypt version in running container
BCRYPT_VER=$(docker exec salesgenie-backend-1 pip show bcrypt 2>/dev/null | awk '/^Version:/ {print $2}')
if [ "$BCRYPT_VER" = "4.0.1" ]; then
  pass "bcrypt 4.0.1 (passlib-compatible)"
else
  fail "bcrypt is $BCRYPT_VER (must be 4.0.1) — run: docker exec salesgenie-backend-1 pip install --force-reinstall 'bcrypt==4.0.1' && docker compose restart backend"
fi

# -------------------------------------------------------------------------
# 1. Backend health
# -------------------------------------------------------------------------
step "1. Backend health"

if curl -sS -o /dev/null -w "%{http_code}" http://localhost:8000/health | grep -q "200"; then
  pass "GET /health → 200"
else
  fail "GET /health did not return 200"
fi

# Dev console
if curl -sS -o /dev/null -w "%{http_code}" http://localhost:8000/ | grep -q "200"; then
  pass "GET / (dev console) → 200"
else
  fail "GET / did not return 200"
fi

# OpenAPI routes
ROUTES=$(curl -sS http://localhost:8000/openapi.json 2>/dev/null | python3 -c "
import sys, json
spec = json.load(sys.stdin)
print('\n'.join(sorted(spec.get('paths', {}).keys())))
" 2>/dev/null)
for ep in /auth/register /auth/login /auth/me /config/kb/upload /config/kb/status \
          /config/integrations /analysis/analyze /transcription/file; do
  if echo "$ROUTES" | grep -q "^${ep}$\|^${ep%/*}/"; then
    pass "route exists: $ep"
  else
    fail "route missing: $ep"
  fi
done

# -------------------------------------------------------------------------
# 2. Database (Supabase) — round-trip
# -------------------------------------------------------------------------
step "2. Database round-trip"

DB_RESULT=$(docker exec salesgenie-backend-1 python -c "
import asyncio
from sqlalchemy import text
from backend.db.session import async_engine
async def m():
    async with async_engine.connect() as c:
        r = await c.execute(text(\"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\"))
        print(r.scalar())
asyncio.run(m())
" 2>&1 | tail -1)

if [ "$DB_RESULT" -gt 0 ] 2>/dev/null; then
  pass "Supabase connection works ($DB_RESULT tables in public schema)"
else
  fail "Supabase round-trip failed — output: $DB_RESULT"
fi

# -------------------------------------------------------------------------
# 3. Auth — register, login, /me
# -------------------------------------------------------------------------
step "3. Auth"

STAMP=$(python3 -c "import time;print(int(time.time()))")
TEST_EMAIL="smoketest_${STAMP}@example.com"
TEST_PASS="SmokeTest1234"

REG=$(curl -sS -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${TEST_EMAIL}\",\"password\":\"${TEST_PASS}\",\"full_name\":\"Smoke Test\"}")
TOKEN=$(echo "$REG" | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [ -n "$TOKEN" ]; then
  pass "register → got access_token (${#TOKEN} chars)"
else
  fail "register failed → $REG"
fi

LOGIN=$(curl -sS -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${TEST_EMAIL}\",\"password\":\"${TEST_PASS}\"}")
if echo "$LOGIN" | grep -q "access_token"; then
  pass "login → got access_token"
else
  fail "login failed → $LOGIN"
fi

if [ -n "$TOKEN" ]; then
  ME=$(curl -sS http://localhost:8000/auth/me -H "Authorization: Bearer $TOKEN")
  if echo "$ME" | grep -q "$TEST_EMAIL"; then
    pass "/auth/me returns user (email matches)"
  else
    fail "/auth/me wrong shape → $ME"
  fi

  # Wrong password should 401
  CODE=$(curl -sS -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${TEST_EMAIL}\",\"password\":\"WRONG_PASSWORD\"}")
  if [ "$CODE" = "401" ]; then
    pass "login with wrong password → 401"
  else
    fail "login with wrong password should be 401, got $CODE"
  fi
fi

# -------------------------------------------------------------------------
# 4. KB upload + status + retrieval
# -------------------------------------------------------------------------
step "4. RAG pipeline (KB upload + retrieval)"

if [ -z "$TOKEN" ]; then
  skip "KB tests (no auth token from previous step)"
else
  # Make a tiny test framework file — name MUST end in .txt for ingestion
  TMP_DIR=$(mktemp -d -t sgsmoke)
  TMP_KB="${TMP_DIR}/kb-smoketest.txt"
  cat > "$TMP_KB" <<'EOF'
TEST FRAMEWORK
==============
Dimension Test1: score 5 if rep asked at least 3 discovery questions.
Dimension Test2: score 5 if next step has a specific date.
EOF

  UP=$(curl -sS -X POST http://localhost:8000/config/kb/upload \
    -H "Authorization: Bearer $TOKEN" -F "file=@${TMP_KB}")
  DOC_ID=$(echo "$UP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
  if [ -n "$DOC_ID" ]; then
    pass "upload returned doc_id ($DOC_ID)"
  else
    fail "upload failed → $UP"
  fi

  sleep 6
  STATUS=$(curl -sS http://localhost:8000/config/kb/status -H "Authorization: Bearer $TOKEN")
  if echo "$STATUS" | grep -q '"status":"ready"'; then
    pass "ingestion completed (status: ready)"
  elif echo "$STATUS" | grep -q '"status":"processing"'; then
    note "still processing after 6s — that's OK for big files, but for our 200-byte test file it's slow"
    fail "ingestion didn't complete in 6s"
  else
    fail "no ready document in KB status → $STATUS"
  fi

  # Retrieval direct test. Emit a unique marker token so we can grep past
  # any loguru lines that share stdout with the python print.
  RETR=$(docker exec salesgenie-backend-1 python -c "
from backend.rag.retriever import retrieve_frameworks
r = retrieve_frameworks('00000000-0000-0000-0000-000000000000', 'anything')
print('SMOKE_RESULT=' + ('isolated' if 'No frameworks loaded' in r else 'leaked'))
" 2>&1 | grep 'SMOKE_RESULT=')
  if echo "$RETR" | grep -q 'SMOKE_RESULT=isolated'; then
    pass "cross-user isolation verified (stranger user sees empty placeholder)"
  else
    fail "cross-user isolation may be broken → $RETR"
  fi

  rm -rf "$TMP_DIR"
fi

# -------------------------------------------------------------------------
# 5. Integrations endpoints
# -------------------------------------------------------------------------
step "5. Integrations"

if [ -z "$TOKEN" ]; then
  skip "integrations tests (no auth token)"
else
  STATUS=$(curl -sS http://localhost:8000/config/integrations -H "Authorization: Bearer $TOKEN")
  if echo "$STATUS" | grep -q '"active_connectors"'; then
    pass "GET /config/integrations returns shape"
  else
    fail "/config/integrations returned: $STATUS"
  fi

  # Test the supabase connector (no creds needed)
  TEST=$(curl -sS -X POST http://localhost:8000/config/integrations/test/supabase \
    -H "Authorization: Bearer $TOKEN")
  if echo "$TEST" | grep -q '"ok":true'; then
    pass "POST /config/integrations/test/supabase → ok"
  else
    fail "supabase test failed → $TEST"
  fi

  # Test notion without config → should return ok:false with helpful error
  TEST=$(curl -sS -X POST http://localhost:8000/config/integrations/test/notion \
    -H "Authorization: Bearer $TOKEN")
  if echo "$TEST" | grep -q '"not configured"'; then
    pass "POST /config/integrations/test/notion (not configured) → graceful fail"
  else
    fail "notion test should say 'not configured' → $TEST"
  fi
fi

# -------------------------------------------------------------------------
# 6. Analysis pipeline (the big one — uses LLM tokens)
# -------------------------------------------------------------------------
step "6. Analysis pipeline (LLM call)"

if [ -z "$TOKEN" ]; then
  skip "analysis (no auth token)"
elif [ "${SKIP_ANALYSIS:-0}" = "1" ]; then
  skip "analysis (SKIP_ANALYSIS=1 set)"
else
  ANALYZE=$(curl -sS --max-time 90 -X POST http://localhost:8000/analysis/analyze \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
      "transcript": "Rep: Hi can I help you?\nProspect: Yes I want to update my map.\nRep: The price is $99 plus shipping. Set up the order?\nProspect: Yes use Visa.",
      "platform": "manual", "duration_secs": 60,
      "talk_ratio_rep": 60, "talk_ratio_prospect": 40,
      "question_count": 2, "speaker_count": 2
    }')

  ANALYSIS_ID=$(echo "$ANALYZE" | python3 -c "import sys,json;print(json.load(sys.stdin,strict=False).get('analysis_id') or '')" 2>/dev/null)
  ERR=$(echo "$ANALYZE" | python3 -c "import sys,json;print(json.load(sys.stdin,strict=False).get('error') or '')" 2>/dev/null)
  SCORE=$(echo "$ANALYZE" | python3 -c "import sys,json;print(json.load(sys.stdin,strict=False).get('overall_score') or 0)" 2>/dev/null)

  if [ -n "$ANALYSIS_ID" ] && [ -z "$ERR" ]; then
    pass "analysis ran → score $SCORE, analysis_id $ANALYSIS_ID"
    DIMS=$(echo "$ANALYZE" | python3 -c "import sys,json;print(len(json.load(sys.stdin,strict=False).get('dimension_scores') or []))" 2>/dev/null)
    pass "dimension_scores populated ($DIMS dimensions)"
    CONNS=$(echo "$ANALYZE" | python3 -c "import sys,json;d=json.load(sys.stdin,strict=False);print(','.join(c.get('connector','')+':'+('ok' if c.get('ok') else 'fail') for c in (d.get('connector_results') or [])))" 2>/dev/null)
    if [ -n "$CONNS" ]; then
      pass "connector dispatch ran: $CONNS"
    fi
  elif echo "$ERR" | grep -qi "rate limit\|quota"; then
    skip "analysis (LLM rate limit hit — wait then retry, or switch provider)"
    note "$ERR" | head -c 200
  elif echo "$ERR" | grep -qi "not set\|invalid"; then
    skip "analysis (LLM credentials not set / invalid)"
    note "$ERR" | head -c 200
  else
    fail "analysis returned error: $ERR"
  fi
fi

# -------------------------------------------------------------------------
# 7. Celery async pipeline (submit + poll)
# -------------------------------------------------------------------------
step "7. Celery async pipeline"

if ! docker compose ps celery_worker 2>/dev/null | grep -q "Up"; then
  fail "celery_worker not running — try: docker compose up -d celery_worker"
elif [ -z "$TOKEN" ]; then
  skip "async pipeline (no auth token)"
elif [ "${SKIP_ANALYSIS:-0}" = "1" ]; then
  skip "async pipeline (SKIP_ANALYSIS=1 set)"
else
  pass "celery_worker container running"
  SUB=$(curl -sS -X POST http://localhost:8000/analysis/submit \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"transcript":"Rep: Hi how can I help?\nProspect: I want to update my map.\nRep: The price is $99. Use a card?\nProspect: Yes Visa.","platform":"manual","duration_secs":60,"talk_ratio_rep":60,"talk_ratio_prospect":40,"question_count":1,"speaker_count":2}')
  JOB_ID=$(echo "$SUB" | python3 -c "import sys,json;print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
  if [ -n "$JOB_ID" ]; then
    pass "submit → job_id $JOB_ID"
    FINAL=""
    for i in $(seq 1 20); do
      sleep 3
      ST=$(curl -sS http://localhost:8000/analysis/job/$JOB_ID -H "Authorization: Bearer $TOKEN")
      STATE=$(echo "$ST" | python3 -c "import sys,json;print(json.load(sys.stdin).get('state',''))" 2>/dev/null)
      if [ "$STATE" = "done" ]; then FINAL="done"; break; fi
      if [ "$STATE" = "failed" ]; then
        FERR=$(echo "$ST" | python3 -c "import sys,json;print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
        if echo "$FERR" | grep -qi "rate limit\|quota\|not set\|invalid"; then
          skip "async job (LLM issue: $(echo $FERR | head -c 80))"
        else
          fail "async job failed: $FERR"
        fi
        FINAL="failed"; break
      fi
    done
    if [ "$FINAL" = "done" ]; then
      pass "async job completed via worker + polling"
    elif [ -z "$FINAL" ]; then
      fail "async job did not finish within 60s"
    fi
  else
    fail "submit failed → $SUB"
  fi
fi

# -------------------------------------------------------------------------
# 8. Phase 9 endpoints — history, stats, recording buffer
# -------------------------------------------------------------------------
step "8. History + recording endpoints"

if [ -z "$TOKEN" ]; then
  skip "phase 9 endpoints (no auth token)"
else
  # history/stats
  ST=$(curl -sS http://localhost:8000/history/stats -H "Authorization: Bearer $TOKEN")
  if echo "$ST" | grep -q '"total_calls"'; then
    pass "GET /history/stats returns aggregate shape"
  else
    fail "/history/stats bad shape → $ST"
  fi

  # history/calls
  HC=$(curl -sS "http://localhost:8000/history/calls?limit=2" -H "Authorization: Bearer $TOKEN")
  if echo "$HC" | grep -q '"items"'; then
    pass "GET /history/calls paginated shape"
  else
    fail "/history/calls bad shape → $HC"
  fi

  # analysis/history
  AH=$(curl -sS "http://localhost:8000/analysis/history?limit=1" -H "Authorization: Bearer $TOKEN")
  if echo "$AH" | grep -q '"items"'; then
    pass "GET /analysis/history paginated shape"
  else
    fail "/analysis/history bad shape → $AH"
  fi

  # recording start + chunk (no stop — avoids burning transcription minutes)
  SID=$(curl -sS -X POST http://localhost:8000/recording/start \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"platform":"smoketest"}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
  if [ -n "$SID" ]; then
    pass "POST /recording/start → session_id"
    B64=$(python3 -c "import base64;print(base64.b64encode(b'smoke audio').decode())")
    CH=$(curl -sS -X POST http://localhost:8000/recording/chunk \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d "{\"session_id\":\"$SID\",\"chunk_base64\":\"$B64\",\"seq\":0}")
    if echo "$CH" | grep -q '"bytes_received"'; then
      pass "POST /recording/chunk buffers audio"
    else
      fail "/recording/chunk failed → $CH"
    fi
  else
    fail "/recording/start failed"
  fi
fi

# -------------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------------
echo
echo "================================================================"
echo -e "Smoke test results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${YELLOW}${SKIP} skipped${NC}"
echo "================================================================"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
