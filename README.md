# Sales Genie

Sales Genie is an agentic AI system that records sales calls in the browser,
transcribes them with speaker diarization, and scores them against **your own
uploaded frameworks** using RAG — then writes results to your database and
fires coaching/intervention alerts. It is a Chrome extension backed by a
FastAPI + LangGraph service, and it is autonomous after a one-time setup.

Unlike generic meeting-notes tools, Sales Genie scores each call against the
methodology *you* upload (MEDDIC, SPICED, a custom rubric, etc.), produces
structured per-dimension scores with evidence quotes, and routes alerts to
managers when a call needs intervention.

---

## Architecture

```
                                 ┌──────────────────────────────┐
   Chrome Extension (MV3)        │         FastAPI backend       │
   ┌───────────────────┐         │                               │
   │ content script    │         │  /auth      JWT + bcrypt      │
   │  (Zoom/Meet/Gong/  │         │  /config    KB + integrations │
   │   Teams detect)    │         │  /recording chunk buffer      │
   │ service worker     │  HTTPS  │  /transcription  AssemblyAI   │
   │  + offscreen doc   │────────▶│  /analysis  run the agent     │
   │  (tabCapture +     │         │  /history   list + stats      │
   │   MediaRecorder)   │         │  /notifications  test         │
   │ sidebar (6 states) │         │                               │
   │ settings / onboard │         └───────┬───────────────┬───────┘
   └───────────────────┘                 │               │
                                          ▼               ▼
                             ┌────────────────────┐  ┌──────────────┐
                             │   LangGraph agent   │  │  Celery +    │
                             │ preprocess          │  │  Redis       │
                             │  → retrieve_kb (RAG)│  │ (async jobs) │
                             │  → classify         │  └──────────────┘
                             │  → score  ◀── LLM   │
                             │  → coach  ◀── LLM   │   LLM: Groq /
                             │  → alert decision   │   Gemini /
                             │  → write_db         │   Anthropic
                             │  → dispatch (fanout)│   (switchable)
                             │  → notify           │
                             └───┬────────┬────────┘
                                 │        │
              ┌──────────────────┘        └──────────────────┐
              ▼                    ▼                 ▼         ▼
       ┌─────────────┐    ┌──────────────┐   ┌──────────┐  ┌────────┐
       │  Supabase   │    │  ChromaDB    │   │ SendGrid │  │ Notion │
       │ (Postgres)  │    │ (per-user    │   │  email   │  │ Sheets │
       │ system of   │    │  RAG vectors)│   │  alerts  │  │(connect│
       │ record      │    │              │   │          │  │ ors)   │
       └─────────────┘    └──────────────┘   └──────────┘  └────────┘
```

**Design principle:** Supabase is the system of record (always written first).
ChromaDB holds per-user framework embeddings. Notion/Sheets are optional
human-facing mirrors. The LLM is provider-agnostic behind a small factory.

---

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (async) |
| Agent | LangGraph |
| LLM | Groq (Llama 3.3 70B) / Google Gemini / Anthropic Claude — switchable |
| Structured output | Pydantic v2 |
| RAG | LangChain splitter + ChromaDB + sentence-transformers (all-MiniLM-L6-v2) |
| Transcription | AssemblyAI (diarized) |
| Auth | JWT (python-jose) + bcrypt; Fernet-encrypted CRM tokens |
| Database | Supabase (Postgres) via SQLAlchemy 2.0 async |
| Migrations | Alembic |
| Async jobs | Celery + Redis |
| Email | SendGrid |
| Connectors | Supabase + Notion + Google Sheets (plugin pattern) |
| Extension | Chrome Manifest V3 (vanilla JS) |
| Deploy | Docker + docker-compose |

---

## Setup

### Prerequisites
- Docker Desktop
- A Supabase project (free tier) — for the Postgres database
- One free LLM key: **Groq** (console.groq.com) or **Google Gemini** (aistudio.google.com/apikey)
- Optional: AssemblyAI key (transcription), SendGrid key (email alerts)

### 1. Configure environment
```bash
cp .env.example .env
```
Fill in `.env` (see the env-var table below). At minimum you need
`DATABASE_URL`, `DATABASE_URL_SYNC`, `JWT_SECRET_KEY`, and one LLM key.

Generate the security keys:
```bash
# JWT secret
openssl rand -hex 32
# Fernet encryption key (for stored CRM tokens)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Run migrations
```bash
docker compose build backend
docker compose run --rm backend alembic upgrade head
```
This creates the six tables in your Supabase project.

### 3. Start the stack
```bash
docker compose up -d
```
Brings up: `backend` (:8000), `redis`, `celery_worker`, `celery_beat`, `flower` (:5555).

- Backend + dev console → http://localhost:8000/
- Celery monitoring (Flower) → http://localhost:5555
- API docs (OpenAPI) → http://localhost:8000/docs

> **Note on Supabase pooler:** if you use Supabase's connection pooler, set
> both `DATABASE_URL` and `DATABASE_URL_SYNC` to the **session pooler**
> (port 5432) — the transaction pooler (6543) breaks prepared statements with
> asyncpg. The username is `postgres.<project-ref>`.

---

## Load the Chrome extension

1. Open **`chrome://extensions`**
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** → select the **`extension/`** folder
4. Pin the Sales Genie icon
5. Click the icon → **Settings** → confirm Backend URL is `http://localhost:8000` → Save
6. Back in the popup → **Create one** to register, or log in

After registering, a **setup wizard** walks you through uploading a framework,
setting alert preferences, and (optionally) connecting Notion/Sheets.

---

## Configuration walkthrough

1. **Register / log in** (extension popup or dev console).
2. **Upload a scoring framework** — a PDF/DOCX/TXT describing your rubric.
   Sample frameworks are in [`kb_samples/`](kb_samples/). Calls are scored
   against *only* your uploaded frameworks (strictly per-user isolated).
3. **Set alert thresholds** — score below `low` → intervention email; at/above
   `high` → coaching email. Optionally set a manager email.
4. **(Optional) Connect Notion / Google Sheets** in Settings so each analysis
   also lands there.
5. **Record a call** on Zoom/Meet/Gong/Teams, or paste a transcript into the
   dev console's Call Analysis panel. Sample transcripts are in
   [`sample_transcripts/`](sample_transcripts/).

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ | Async Postgres URL (`postgresql+asyncpg://…`) |
| `DATABASE_URL_SYNC` | ✅ | Sync Postgres URL for Alembic/Celery (`postgresql+psycopg2://…`) |
| `SUPABASE_URL` / `SUPABASE_PROJECT_REF` | – | Used to build dashboard deep-links |
| `JWT_SECRET_KEY` | ✅ | Signs JWTs (`openssl rand -hex 32`) |
| `ENCRYPTION_KEY` | for connectors | Fernet key encrypting stored Notion/Sheets/Slack tokens |
| `LLM_PROVIDER` | ✅ | `groq` \| `google` \| `anthropic` |
| `GROQ_API_KEY` | if groq | Free — console.groq.com |
| `GOOGLE_API_KEY` | if google | Free — aistudio.google.com/apikey |
| `ANTHROPIC_API_KEY` | if anthropic | console.anthropic.com |
| `LLM_PROVIDER_SCORE` etc. | – | Per-node provider override (route only scoring to a stronger model) |
| `ASSEMBLYAI_API_KEY` | for recording | Diarized transcription |
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | for email | Alert emails (verified sender required) |
| `REDIS_URL` | ✅ | Celery broker + result backend + job status |
| `CHROMA_PERSIST_DIR` | – | ChromaDB path (default `./chroma_db`) |

Full annotated list in [`.env.example`](.env.example).

---

## Running tests

```bash
# pytest suite (35 tests: agent, RAG, connectors, API)
docker compose run --rm \
  -v "$(pwd)/tests:/app/tests" \
  -v "$(pwd)/pytest.ini:/app/pytest.ini" \
  backend pytest

# end-to-end smoke test (hits every live endpoint)
bash scripts/smoke_test.sh
# skip the LLM call to save token budget:
SKIP_ANALYSIS=1 bash scripts/smoke_test.sh
```

The LLM is mocked in the pytest suite, so no provider tokens are spent. DB-backed
tests skip automatically if the database is unreachable.

---

## Adding a new CRM connector (plugin pattern)

Connectors implement one small interface and are auto-discovered by the factory.
To add, say, Salesforce:

**1. Create `backend/connectors/salesforce_connector.py`:**
```python
from backend.connectors.base import AnalysisPayload, BaseConnector, ConnectorResult

class SalesforceConnector(BaseConnector):
    name = "salesforce"

    async def write_analysis(self, payload: AnalysisPayload) -> ConnectorResult:
        try:
            # ... push payload to Salesforce via its API ...
            return ConnectorResult(connector=self.name, ok=True, external_url=record_url)
        except Exception as exc:          # never raise — degrade gracefully
            return ConnectorResult(connector=self.name, ok=False, error=str(exc))

    async def test_connection(self, user_id) -> ConnectorResult:
        ...
```

**2. Register it in `backend/connectors/factory.py`** inside `get_connectors()`:
```python
if integ.salesforce_token:
    connectors.append(SalesforceConnector())
```

That's it. The agent's `dispatch_connectors` node fans out to every configured
connector in parallel via `asyncio.gather`, and a failing connector returns a
`ConnectorResult(ok=False)` without taking down the others. Credentials live in
the `user_integrations` table, encrypted at rest with Fernet.

---

## Project layout

```
backend/
  agent/        LangGraph state, nodes, graph, LLM factory
  api/          FastAPI routers (auth, config, recording, transcription, analysis, history, notifications)
  connectors/   base + supabase/notion/sheets + factory
  db/           SQLAlchemy models, session, Alembic migrations
  notifications/ SendGrid email + Slack Block Kit
  rag/          ingestion, chunking, embeddings, vectorstore, retriever
  tasks/        Celery app + analysis/notification tasks
  transcription/ AssemblyAI client, realtime relay, processor
  static/       dev console (single-file test UI)
extension/      Chrome MV3 extension (background, content, sidebar, config)
kb_samples/     example scoring frameworks to upload
sample_transcripts/  example calls to score
tests/          pytest suite
scripts/        smoke_test.sh
```

---

## API docs

Interactive OpenAPI docs at **http://localhost:8000/docs** when the backend is
running.
