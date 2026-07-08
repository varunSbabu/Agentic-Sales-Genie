#!/usr/bin/env bash
# Production entrypoint — runs DB migrations, then starts the requested process.
# Usage (set as the container command):
#   scripts/entrypoint.sh web       → migrate + uvicorn (gunicorn workers)
#   scripts/entrypoint.sh worker    → celery worker
#   scripts/entrypoint.sh beat      → celery beat
#   scripts/entrypoint.sh flower    → flower monitoring
set -euo pipefail

ROLE="${1:-web}"
WORKERS="${WEB_CONCURRENCY:-2}"
PORT="${PORT:-8000}"

run_migrations() {
  echo "[entrypoint] running alembic migrations…"
  alembic upgrade head
}

case "$ROLE" in
  web)
    run_migrations
    echo "[entrypoint] starting uvicorn ($WORKERS workers) on :$PORT"
    exec uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" --workers "$WORKERS"
    ;;
  worker)
    echo "[entrypoint] starting celery worker"
    exec celery -A backend.tasks.celery_app worker --loglevel=info --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    echo "[entrypoint] starting celery beat"
    exec celery -A backend.tasks.celery_app beat --loglevel=info
    ;;
  flower)
    echo "[entrypoint] starting flower on :${FLOWER_PORT:-5555}"
    exec celery -A backend.tasks.celery_app flower --port="${FLOWER_PORT:-5555}" \
      ${FLOWER_BASIC_AUTH:+--basic_auth="$FLOWER_BASIC_AUTH"}
    ;;
  *)
    echo "[entrypoint] unknown role: $ROLE (expected web|worker|beat|flower)" >&2
    exit 1
    ;;
esac
