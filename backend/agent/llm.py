"""Provider-agnostic LLM factory for agent nodes.

The spec specifies Anthropic Claude. In practice, swapping providers is
a one-config-line change because LangChain's `BaseChatModel` API is uniform
across providers, and `.with_structured_output()` works the same way.

Currently supports:
  - anthropic (claude-sonnet-4-6, requires paid credits)
  - groq (llama-3.3-70b-versatile, free with rate limits)

To add another (Gemini, OpenAI, Mistral, etc.) just import the provider's
LangChain adapter and add a branch — the rest of the agent code doesn't
change.
"""

from langchain_core.language_models.chat_models import BaseChatModel

from backend.config import settings


class LLMConfigError(RuntimeError):
    """Raised when the configured LLM provider can't be initialised."""


_PURPOSE_OVERRIDES = {
    "score": "llm_provider_score",
    "classify": "llm_provider_classify",
    "coach": "llm_provider_coach",
}


def _resolve_provider(purpose: str | None) -> str:
    """Pick the provider for this purpose. Per-purpose override wins over
    the global `LLM_PROVIDER`, both env-driven. Empty / unset → default.
    """
    if purpose in _PURPOSE_OVERRIDES:
        override = getattr(settings, _PURPOSE_OVERRIDES[purpose], "") or ""
        if override.strip():
            return override.strip().lower()
    return (settings.llm_provider or "google").lower()


def get_llm(
    *,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    purpose: str | None = None,
) -> BaseChatModel:
    """Build a chat model from the configured provider.

    `purpose` ("score" | "classify" | "coach") allows routing one node to
    a different provider than the rest — e.g. set LLM_PROVIDER_SCORE=anthropic
    to spend on Claude only for the high-stakes scoring step.

    Both Anthropic, Groq, and Google support tool/function calling, which is
    what `with_structured_output()` uses to enforce the Pydantic schemas.
    """
    provider = _resolve_provider(purpose)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMConfigError(
                "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set. "
                "Either fund the Anthropic account and add the key, or switch "
                "LLM_PROVIDER to 'groq' in .env."
            )
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if provider == "groq":
        if not settings.groq_api_key:
            raise LLMConfigError(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set. "
                "Sign up free at https://console.groq.com (no card required), "
                "copy your key, and add GROQ_API_KEY=... to .env."
            )
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if provider == "google":
        if not settings.google_api_key:
            raise LLMConfigError(
                "LLM_PROVIDER=google but GOOGLE_API_KEY is not set. "
                "Get a free key at https://aistudio.google.com/apikey "
                "(no card required), then add GOOGLE_API_KEY=... to .env."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.google_model,
            google_api_key=settings.google_api_key,
            temperature=temperature,
            max_output_tokens=max_tokens,
            # Note: Gemini's safety filters can occasionally flag sales
            # language ("discount", "pressure", "expires"). If you see
            # 'blocked due to safety' in logs, set safety_settings here
            # using the langchain_google_genai HarmCategory enums.
        )

    raise LLMConfigError(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'anthropic' | 'groq' | 'google'."
    )


def llm_available(purpose: str | None = None) -> bool:
    """Cheap check used by nodes to short-circuit before an API call.

    Respects per-purpose routing so a node configured to use Anthropic
    correctly reports unavailable when only the Anthropic key is missing.
    """
    p = _resolve_provider(purpose)
    if p == "anthropic":
        return bool(settings.anthropic_api_key)
    if p == "groq":
        return bool(settings.groq_api_key)
    if p == "google":
        return bool(settings.google_api_key)
    return False


def coerce_structured(raw, schema):
    """Normalise whatever `with_structured_output()` returned into a Pydantic instance.

    LangChain providers disagree on this:
      - langchain-anthropic   → returns the Pydantic instance directly
      - langchain-groq        → often returns a plain dict for Llama models
      - langchain-google-genai → returns Pydantic when method='function_calling'

    Calling code then does `result.field` — which crashes when raw is a dict.
    This helper makes the agent nodes provider-portable.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return schema.model_validate(raw)
    return raw


def missing_key_message(purpose: str | None = None) -> str:
    p = _resolve_provider(purpose)
    if p == "anthropic":
        return "ANTHROPIC_API_KEY not set"
    if p == "groq":
        return "GROQ_API_KEY not set — sign up free at https://console.groq.com"
    if p == "google":
        return "GOOGLE_API_KEY not set — sign up free at https://aistudio.google.com/apikey"
    return f"LLM_PROVIDER={p!r} is unrecognised"
