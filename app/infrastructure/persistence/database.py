"""Async SQLAlchemy engine and session factory.

Supports PostgreSQL (production) and SQLite (development/testing).
"""
from __future__ import annotations
import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def build_engine(database_url: str, echo: bool = False) -> AsyncEngine:
    """Create async engine. Auto-detects SQLite vs PostgreSQL."""
    kwargs: dict = {
        "echo": echo,
        "future": True,
    }

    if database_url.startswith("sqlite"):
        # Convert sync sqlite:/// → async sqlite+aiosqlite:///
        if not database_url.startswith("sqlite+aiosqlite"):
            database_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        kwargs.update({
            "connect_args": {"check_same_thread": False},
            "poolclass":    StaticPool,
        })
    elif database_url.startswith("postgresql"):
        # Convert sync postgresql:// → async postgresql+asyncpg://
        if not database_url.startswith("postgresql+asyncpg"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        kwargs.update({
            "pool_size":     10,
            "max_overflow":  20,
            "pool_pre_ping": True,
            "pool_recycle":  3600,
        })

    return create_async_engine(database_url, **kwargs)


def init_async_db(database_url: str, echo: bool = False) -> None:
    """Initialise engine and session factory. Call once at startup."""
    global _engine, _session_factory
    _engine = build_engine(database_url, echo=echo)
    _session_factory = async_sessionmaker(
        bind=_engine, class_=AsyncSession, expire_on_commit=False,
    )
    logger.info("Async DB engine initialised: %s", database_url.split("@")[-1] if "@" in database_url else database_url)


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB not initialised — call init_async_db() at startup")
    return _engine


def get_session_factory() -> async_sessionmaker:
    if _session_factory is None:
        raise RuntimeError("DB not initialised — call init_async_db() at startup")
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an AsyncSession per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
