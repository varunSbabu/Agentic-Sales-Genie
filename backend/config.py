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
    return Settings()


settings = get_settings()
