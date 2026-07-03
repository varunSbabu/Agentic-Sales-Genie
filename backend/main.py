"""Sales Genie FastAPI app entry point."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api.analysis import router as analysis_router
from backend.api.auth import limiter as auth_limiter
from backend.api.auth import router as auth_router
from backend.api.configuration import router as config_router
from backend.api.history import router as history_router
from backend.api.notifications import router as notifications_router
from backend.api.recording import router as recording_router
from backend.api.transcription import router as transcription_router
from backend.config import settings
from backend.utils.logging import configure_logging, logger

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Sales Genie API starting (env={})", settings.app_env)
    yield
    logger.info("Sales Genie API shutting down")


app = FastAPI(
    title="Sales Genie API",
    description="Agentic sales-call intelligence backend.",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Rate limiting -----------------------------------------------------------
# SlowAPI hooks via app.state.limiter and an exception handler. We register
# the auth router's Limiter here so the @limiter.limit decorators take effect.
app.state.limiter = auth_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS --------------------------------------------------------------------
# A Chrome extension gets a random chrome-extension://<id> origin that we can't
# know ahead of time, so we allow all chrome-extension origins via regex plus
# localhost for the dev console. In production, pin EXTENSION_ORIGIN and drop
# the regex.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.extension_origin,
        "http://localhost:8000",
        "http://localhost:5173",
    ],
    allow_origin_regex=r"^chrome-extension://[a-p]+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers -----------------------------------------------------------------
app.include_router(auth_router)
app.include_router(config_router)
app.include_router(transcription_router)
app.include_router(analysis_router)
app.include_router(notifications_router)
app.include_router(recording_router)
app.include_router(history_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


# --- Dev Console (static SPA) ------------------------------------------------
# Serve a single-file dev UI at "/" for manual testing. Mounted last so router
# paths win the route resolution order.
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def dev_console() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
