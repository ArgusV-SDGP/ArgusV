"""
tests/conftest.py — Shared pytest fixtures
Task: TEST-01
"""
import pytest
import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

# Add src directory to Python path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

try:
    from bus import bus, EventBus
except ImportError:
    # Provide fallback if bus module not found
    EventBus = MagicMock


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


# ── ORM model fixtures ────────────────────────────────────────────────────

import uuid
from datetime import datetime
from db.models import Camera, Zone, Segment, Incident, Detection, Rule


@pytest.fixture
def sample_camera(db_session):
    cam = Camera(
        camera_id="cam-test-01",
        name="Test Camera",
        rtsp_url="rtsp://localhost:8554/test",
        status="online",
    )
    db_session.add(cam)
    db_session.commit()
    return cam


@pytest.fixture
def sample_zone(db_session):
    zone = Zone(
        zone_id=uuid.uuid4(),
        name="Test Zone",
        polygon_coords=[[0, 0], [1, 0], [1, 1], [0, 1]],
        zone_type="intrusion",
        active=True,
    )
    db_session.add(zone)
    db_session.commit()
    return zone


@pytest.fixture
def sample_segment(db_session, sample_camera):
    seg = Segment(
        segment_id=uuid.uuid4(),
        camera_id=sample_camera.camera_id,
        start_time=datetime.utcnow(),
        end_time=datetime.utcnow(),
        duration_sec=10.0,
        minio_path="/recordings/test-segment.mp4",
    )
    db_session.add(seg)
    db_session.commit()
    return seg


@pytest.fixture
def sample_incident(db_session, sample_camera):
    inc = Incident(
        incident_id=uuid.uuid4(),
        camera_id=sample_camera.camera_id,
        object_class="person",
        threat_level="HIGH",
        summary="Test incident — suspicious loitering detected",
        status="OPEN",
        detected_at=datetime.utcnow(),
    )
    db_session.add(inc)
    db_session.commit()
    return inc


@pytest.fixture
def sample_detection(db_session, sample_camera, sample_incident):
    det = Detection(
        detection_id=uuid.uuid4(),
        event_id="evt-test-01",
        camera_id=sample_camera.camera_id,
        incident_id=sample_incident.incident_id,
        detected_at=datetime.utcnow(),
        object_class="person",
        confidence=0.92,
        is_threat=True,
        threat_level="HIGH",
    )
    db_session.add(det)
    db_session.commit()
    return det


@pytest.fixture
def sample_rule(db_session, sample_zone):
    rule = Rule(
        rule_id=uuid.uuid4(),
        zone_id=sample_zone.zone_id,
        trigger_type="intrusion",
        severity="HIGH",
        action_config={"notify": True},
        is_active=True,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


@pytest.fixture
def mock_db():
    """Mock SQLAlchemy session."""
    return MagicMock()


@pytest.fixture
def mock_redis():
    """Mock redis client — prevents real Redis connection in tests."""
    r = MagicMock()
    r.get.return_value = None
    r.set.return_value = True
    r.publish.return_value = 1
    r.exists.return_value = True
    r.delete.return_value = 1
    return r


@pytest.fixture
def mock_vlm():
    """Mock VLM response — prevents real OpenAI calls in tests."""
    return AsyncMock(return_value={
        "threat_level": "LOW",
        "is_threat": False,
        "summary": "Mocked VLM response — no threat detected.",
        "recommended_action": "NONE",
    })


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
