"""Celery application + Redis-backed job-status helpers.

Redis plays two roles here:
  1. Celery broker + result backend (task queue + return values)
  2. A lightweight per-job status store (`job:{job_id}` → JSON) that the API
     polls so the client sees granular progress (queued → analyzing → done)
     rather than just Celery's coarse PENDING/SUCCESS states.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, TypeVar

import redis
from celery import Celery

from backend.config import settings

_T = TypeVar("_T")


def run_async(coro: Awaitable[_T]) -> _T:
    """Run a coroutine to completion from a synchronous Celery task.

    Celery tasks are synchronous, so each invocation spins up a fresh event
    loop via asyncio.run. The shared async DB engine pools connections, and an
    asyncpg connection is bound to the loop it was created on — so a connection
    pooled by one task's loop blows up when reused on the next task's loop
    ("got Future ... attached to a different loop"). Disposing the engine at the
    end of every task guarantees the next task opens fresh connections on its
    own loop.
    """
    async def _wrap() -> _T:
        from backend.db.session import async_engine
        try:
            return await coro
        finally:
            await async_engine.dispose()

    return asyncio.run(_wrap())

celery_app = Celery(
    "sales_genie",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "backend.tasks.analysis_tasks",
        "backend.tasks.notification_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=60 * 60 * 24,  # keep results 24h
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # analysis tasks are long — don't over-prefetch
    timezone="UTC",
)


# ---------------------------------------------------------------------------
# Job-status store — separate from Celery's own result backend so we can emit
# fine-grained progress the client can render.
# ---------------------------------------------------------------------------
_JOB_TTL_SECONDS = 60 * 60 * 6  # 6 hours


def _redis() -> "redis.Redis":
    # Sync client — celery tasks run in a sync context.
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _job_key(job_id: str) -> str:
    return f"job:{job_id}"


def set_job_status(
    job_id: str,
    *,
    state: str,
    step: str = "",
    progress: int = 0,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Write the current status of a job. Called at each pipeline milestone.

    state: queued | running | done | failed
    progress: 0-100 for a UI progress bar
    """
    payload = {
        "job_id": job_id,
        "state": state,
        "step": step,
        "progress": progress,
        "result": result,
        "error": error,
    }
    r = _redis()
    try:
        r.set(_job_key(job_id), json.dumps(payload), ex=_JOB_TTL_SECONDS)
    finally:
        r.close()


def get_job_status(job_id: str) -> dict[str, Any] | None:
    r = _redis()
    try:
        raw = r.get(_job_key(job_id))
    finally:
        r.close()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
