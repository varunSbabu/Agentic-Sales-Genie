"""Lightweight input validators shared across API request models."""

from __future__ import annotations

import re

PASSWORD_MIN_LEN = 10
_PASSWORD_HAS_LETTER = re.compile(r"[A-Za-z]")
_PASSWORD_HAS_DIGIT = re.compile(r"\d")


def validate_password_strength(password: str) -> str:
    """Raise ValueError if password is too weak. Returns the password unchanged."""
    if len(password) < PASSWORD_MIN_LEN:
        raise ValueError(f"password must be at least {PASSWORD_MIN_LEN} characters")
    if not _PASSWORD_HAS_LETTER.search(password):
        raise ValueError("password must contain at least one letter")
    if not _PASSWORD_HAS_DIGIT.search(password):
        raise ValueError("password must contain at least one digit")
    return password


MAX_TRANSCRIPT_CHARS = 100_000


def truncate_transcript(text: str) -> str:
    """Cap transcript size before LLM injection."""
    if len(text) <= MAX_TRANSCRIPT_CHARS:
        return text
    return text[:MAX_TRANSCRIPT_CHARS]
