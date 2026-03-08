"""
db/connection.py — Database session management
-----------------------------------------------
Provides both sync (for FastAPI endpoints) and
async (for pipeline workers) DB sessions.
"""

from contextlib import asynccontextmanager, contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session

import config as cfg
from db.models import Base

# ── Sync engine (FastAPI REST, config, health) ────────────────────────────────
_sync_engine = create_engine(cfg.POSTGRES_URL, pool_pre_ping=True, pool_size=5)
_SyncSession  = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)


def get_db_sync() -> Session:
    """Return a raw sync Session. Caller must call .close()."""
    return _SyncSession()


def get_db():
    """FastAPI Depends() generator."""
    db = _SyncSession()
    try:
        yield db
    finally:
        db.close()


# ── Async engine (pipeline workers) ──────────────────────────────────────────
_ASYNC_URL = cfg.POSTGRES_URL.replace("postgresql://", "postgresql+asyncpg://")
_async_engine  = create_async_engine(_ASYNC_URL, pool_pre_ping=True)
_AsyncSession  = sessionmaker(_async_engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_db_session():
    """Async context manager for pipeline workers."""
    async with _AsyncSession() as session:
        yield session


# ── Schema init ───────────────────────────────────────────────────────────────

def create_tables():
    """Create all tables if they don't exist (dev convenience, use Alembic in prod)."""
    with _sync_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(_sync_engine)
