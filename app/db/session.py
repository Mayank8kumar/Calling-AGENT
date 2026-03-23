"""
# Database connection management
# SQLAlchemy: async engine (asyncpg), session factory, connection pooling
# Redis: two pools — primary (pub/sub, locks) + cache (separate DB for flush)
# RLS: current_tenant_id ContextVar — set per-request, used in get_db()
#   to execute SET LOCAL app.current_tenant for PostgreSQL Row-Level Security
# Cleanup: close_db(), close_redis() called on shutdown
"""

"""
Database session management — async SQLAlchemy engine + Redis pool.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# ContextVar for per-request tenant isolation
current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)

# Module-level singletons — initialized on first access
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_redis_pool: aioredis.Redis | None = None
_redis_cache_pool: aioredis.Redis | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session with automatic tenant RLS setup."""
    session = get_session_factory()()
    try:
        tenant_id = current_tenant_id.get()
        if tenant_id:
            # Set PostgreSQL session variable for Row-Level Security policies
            await session.execute(
                f"SET LOCAL app.current_tenant = '{tenant_id}'"  # noqa: S608
            )
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

def get_redis() -> aioredis.Redis:
    """Primary Redis connection (pub/sub, locks, real-time state)."""
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


def get_redis_cache() -> aioredis.Redis:
    """Dedicated Redis connection for caching (separate DB for easy flush)."""
    global _redis_cache_pool
    if _redis_cache_pool is None:
        settings = get_settings()
        _redis_cache_pool = aioredis.from_url(
            settings.redis_url.rsplit("/", 1)[0] + f"/{settings.redis_cache_db}",
            decode_responses=True,
            max_connections=30,
        )
    return _redis_cache_pool


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


async def close_redis() -> None:
    global _redis_pool, _redis_cache_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None
    if _redis_cache_pool:
        await _redis_cache_pool.aclose()
        _redis_cache_pool = None