"""
tests/conftest.py — Shared pytest fixtures
Task: TEST-01
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

from bus import EventBus
from db.models import Base


# ── SQLite compatibility shims for Postgres-specific column types ──────────
# These teach SQLAlchemy how to render Postgres types as plain SQL for SQLite.

@compiles(UUID, "sqlite")
def _uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"

@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(Vector, "sqlite")
def _vector_sqlite(type_, compiler, **kw):
    return "TEXT"


# ── Core fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db_session():
    """In-memory SQLite session with all ORM tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def mock_bus():
    """Fresh EventBus for each test."""
    return EventBus()


@pytest.fixture
def mock_db():
    """Mock SQLAlchemy session."""
    return MagicMock()


@pytest.fixture
def sample_detection_event():
    return {
        "event_id":     "test-abc-123",
        "event_type":   "LOITERING",
        "camera_id":    "cam-01",
        "timestamp":    1709150407.3,
        "object_class": "person",
        "confidence":   0.87,
        "track_id":     42,
        "zone_id":      "zone-abc",
        "zone_name":    "Parking Lot",
        "dwell_sec":    31,
        "bbox":         {"x1": 120, "y1": 200, "x2": 280, "y2": 600},
    }
