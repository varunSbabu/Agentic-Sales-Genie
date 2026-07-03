"""Alembic env using the sync DATABASE_URL_SYNC for migration execution.

We deliberately do NOT call config.set_main_option("sqlalchemy.url", ...)
because Alembic stores the URL in a ConfigParser section that performs
%-interpolation. A URL-encoded password containing `%23` (#) or `%40` (@)
trips ConfigParser with `invalid interpolation syntax`. Instead, we read
DATABASE_URL_SYNC straight from pydantic settings and pass it to
context.configure() / create_engine().
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend.config import settings
from backend.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        settings.database_url_sync,
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
