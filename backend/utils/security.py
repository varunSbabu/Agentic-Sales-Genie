"""Cryptographic primitives for Sales Genie.

Three concerns live here, intentionally separated:

1. Password hashing (bcrypt via passlib).
2. JWT issuance + decoding (python-jose).
3. Symmetric encryption for stored third-party tokens (Fernet/AES-128-CBC + HMAC).

We deliberately avoid importing this module at app startup so unit tests that
don't touch crypto can run without `JWT_SECRET_KEY` / `ENCRYPTION_KEY` set.
The keys are validated lazily on first use.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.config import settings

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
# bcrypt with the spec's required 12 salt rounds.
_pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)


def hash_password(plain: str) -> str:
    if not plain:
        raise ValueError("password cannot be empty")
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a token cannot be decoded or has invalid claims."""


def _require_jwt_secret() -> str:
    key = settings.jwt_secret_key
    if not key or key == "change-me-in-production":
        if settings.app_env == "production":
            raise RuntimeError(
                "JWT_SECRET_KEY must be set to a strong value in production."
            )
        # In dev/test we still allow the placeholder but warn loudly via the
        # exception message if a bad key is somehow used in prod-like flows.
    return key or "change-me-in-production"


def _create_token(subject: str, token_type: TokenType, lifetime: timedelta) -> str:
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "type": token_type,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, _require_jwt_secret(), algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | uuid.UUID) -> str:
    return _create_token(
        str(subject),
        "access",
        timedelta(minutes=settings.jwt_access_token_minutes),
    )


def create_refresh_token(subject: str | uuid.UUID) -> str:
    return _create_token(
        str(subject),
        "refresh",
        timedelta(days=settings.jwt_refresh_token_days),
    )


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode + validate a JWT. Raises TokenError on any failure.

    Always pass `expected_type` to prevent refresh tokens from being used as
    access tokens (or vice versa) — that's a common JWT vulnerability.
    """
    try:
        payload = jwt.decode(
            token,
            _require_jwt_secret(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise TokenError(f"invalid token: {exc}") from exc

    if expected_type is not None and payload.get("type") != expected_type:
        raise TokenError(
            f"wrong token type: expected {expected_type}, got {payload.get('type')}"
        )

    sub = payload.get("sub")
    if not sub:
        raise TokenError("token missing subject")

    return payload


# ---------------------------------------------------------------------------
# Symmetric encryption for stored third-party tokens
# ---------------------------------------------------------------------------
_fernet_singleton: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazily build the Fernet instance from settings.encryption_key."""
    global _fernet_singleton
    if _fernet_singleton is not None:
        return _fernet_singleton
    key = settings.encryption_key
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        _fernet_singleton = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:  # ValueError on malformed key
        raise RuntimeError(f"ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc
    return _fernet_singleton


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret string (e.g. a Notion or Slack token) for DB storage."""
    if plaintext is None:
        raise ValueError("cannot encrypt None")
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a value previously produced by encrypt_secret."""
    if ciphertext is None:
        raise ValueError("cannot decrypt None")
    try:
        plain = _get_fernet().decrypt(ciphertext.encode("utf-8"))
    except InvalidToken as exc:
        raise RuntimeError("invalid ciphertext — wrong ENCRYPTION_KEY?") from exc
    return plain.decode("utf-8")
