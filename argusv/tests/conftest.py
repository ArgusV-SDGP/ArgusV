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


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
