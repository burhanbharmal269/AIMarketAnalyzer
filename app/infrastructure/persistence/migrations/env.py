"""Alembic env.py — supports both online and offline migration modes.

DATABASE_URL env var overrides alembic.ini sqlalchemy.url.
Supports PostgreSQL (asyncpg) and SQLite (aiosqlite) via sync fallback for migrations.
"""
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

# Import all models so Alembic can detect schema changes
from app.infrastructure.persistence.models import Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# DATABASE_URL env var takes priority over alembic.ini
database_url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url", "")

# Convert async URLs to sync for Alembic (it runs sync migrations internally)
def _sync_url(url: str) -> str:
    return (
        url
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("sqlite+aiosqlite:///",   "sqlite:///")
    )

sync_url = _sync_url(database_url) if database_url else "sqlite:///./data/research.db"


def run_migrations_offline() -> None:
    """Run migrations without a database connection — generates SQL script."""
    context.configure(
        url=sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = sync_url

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
