"""Async SQLAlchemy engine + session factory + FastAPI dependency.

Note on Supabase pooler compatibility: the transaction-mode pgbouncer on port
6543 does not support prepared statements (they're session state). We disable
asyncpg's prepared-statement cache via `statement_cache_size=0` so the engine
works correctly through the pooler. This is a no-op overhead-wise on direct
connections.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import settings


def _is_pooled(url: str) -> bool:
    """Detect Supabase pgbouncer hosts so we can disable prepared statements."""
    return "pooler.supabase.com" in url or "pgbouncer" in url


_connect_args: dict = {}
if _is_pooled(settings.database_url):
    _connect_args = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }

async_engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a request-scoped AsyncSession."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
