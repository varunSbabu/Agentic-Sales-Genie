"""Application settings loaded from environment variables via pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App -----------------------------------------------------------------
    app_env: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    extension_origin: str = "chrome-extension://__REPLACE_WITH_EXTENSION_ID__"
    frontend_url: str = "http://localhost:8000"
    # Public URL the backend is reachable at (used for links in emails/Slack).
    # In production set this to your deployed HTTPS URL, e.g. https://api.yourdomain.com
    public_base_url: str = "http://localhost:8000"
    # Comma-separated extra CORS origins to allow (e.g. your dashboard domain).
    cors_origins: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        base = [
            self.extension_origin,
            self.frontend_url,
            self.public_base_url,
            "http://localhost:8000",
            "http://localhost:5173",
        ]
        extra = [o.strip() for o in (self.cors_origins or "").split(",") if o.strip()]
        # de-dupe, drop the unreplaced placeholder
        seen, out = set(), []
        for o in base + extra:
            if o and "__REPLACE_WITH_EXTENSION_ID__" not in o and o not in seen:
                seen.add(o)
                out.append(o)
        return out

    # --- LLM (provider-agnostic) --------------------------------------------
    # Default provider for agent nodes. "google" and "groq" are free.
    llm_provider: Literal["anthropic", "groq", "google"] = "google"

    # Optional per-node overrides — useful for routing only the high-stakes
    # score_node to a stronger model (e.g. Claude) while keeping classify/coach
    # on the free tier. Leave blank to fall back to llm_provider.
    llm_provider_score: str = ""
    llm_provider_classify: str = ""
    llm_provider_coach: str = ""

    # --- Anthropic -----------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # --- Groq (free tier) ----------------------------------------------------
    # Sign up at https://console.groq.com — no card required.
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Google (Gemini, free tier) ------------------------------------------
    # Sign up at https://aistudio.google.com/apikey — no card required.
    google_api_key: str = ""
    google_model: str = "gemini-2.0-flash"

    # --- AssemblyAI ----------------------------------------------------------
    assemblyai_api_key: str = ""

    # --- Database ------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/postgres"
    )
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_project_ref: str = ""
    # New-style API keys (Supabase Settings → API Keys)
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    # Legacy keys (Supabase Settings → API → Legacy tab) — kept for SDKs that still need them
    supabase_anon_key: str = ""
    supabase_service_key: str = ""

    # --- Redis / Celery ------------------------------------------------------
    redis_url: str = "redis://redis:6379/0"

    # --- Cloudflare R2 -------------------------------------------------------
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "sales-genie-recordings"
    r2_public_base_url: str = ""

    # --- SendGrid ------------------------------------------------------------
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "alerts@example.com"
    sendgrid_from_name: str = "Sales Genie"

    # --- LangSmith -----------------------------------------------------------
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "sales-genie"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    # --- Security ------------------------------------------------------------
    jwt_secret_key: str = "change-me-in-production"
    encryption_key: str = ""
    jwt_access_token_minutes: int = 15
    jwt_refresh_token_days: int = 7
    jwt_algorithm: str = "HS256"

    # --- Slack ---------------------------------------------------------------
    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    # --- RAG -----------------------------------------------------------------
    chroma_persist_dir: str = "./chroma_db"
    embedding_model: str = "all-MiniLM-L6-v2"
    chunk_size: int = 512
    chunk_overlap: int = 50
    retrieval_k: int = 5


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    _validate_production(s)
    return s


def _validate_production(s: Settings) -> None:
    """Fail fast on unsafe config when running in production."""
    if s.app_env != "production":
        return
    problems: list[str] = []
    if not s.jwt_secret_key or s.jwt_secret_key == "change-me-in-production":
        problems.append("JWT_SECRET_KEY must be set to a strong value")
    if not s.encryption_key:
        problems.append("ENCRYPTION_KEY must be set (CRM tokens are stored encrypted)")
    if not s.database_url or "localhost" in s.database_url:
        problems.append("DATABASE_URL must point at your production database")
    if s.public_base_url.startswith("http://") and "localhost" not in s.public_base_url:
        problems.append("PUBLIC_BASE_URL should be https:// in production")
    provider_key = {
        "groq": s.groq_api_key,
        "google": s.google_api_key,
        "anthropic": s.anthropic_api_key,
    }.get(s.llm_provider, "")
    if not provider_key:
        problems.append(f"LLM_PROVIDER={s.llm_provider} but its API key is not set")
    if problems:
        raise RuntimeError(
            "Refusing to start in production with unsafe config:\n  - "
            + "\n  - ".join(problems)
        )


settings = get_settings()
