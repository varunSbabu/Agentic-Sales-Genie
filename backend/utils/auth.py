"""FastAPI auth dependencies — resolve the current user from a Bearer JWT."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import User
from backend.db.session import get_db
from backend.utils.security import TokenError, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def _credentials_exception(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_id(
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> uuid.UUID:
    """Decode the JWT and return the user_id — does NOT hit the database."""
    if not token:
        raise _credentials_exception("Missing authentication token")
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError as exc:
        raise _credentials_exception(str(exc)) from exc
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _credentials_exception("Invalid token subject") from exc


async def get_current_user(
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Resolve the JWT to a loaded User row — hits the DB."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise _credentials_exception("User no longer exists")
    return user


CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
CurrentUser = Annotated[User, Depends(get_current_user)]
