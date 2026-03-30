"""
tests/test_stats_api.py - Stats API tests aligned with current payload contract.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Add src to path
import sys
from pathlib import Path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from api.routes.stats import router as stats_router
from db.connection import get_db


def _make_query(**kwargs):
    q = MagicMock()
    q.filter.return_value = q
    q.group_by.return_value = q
    q.order_by.return_value = q
    for key, value in kwargs.items():
        setattr(q, key, MagicMock(return_value=value))
    return q


@pytest.fixture
def mock_db_session():
    session = MagicMock()

    camera_rows = [
        SimpleNamespace(camera_id="cam-01", name="Front Gate", status="online", resolution="1920x1080", fps=25),
        SimpleNamespace(camera_id="cam-02", name="Parking Lot", status="offline", resolution="1280x720", fps=0),
    ]
    latest_incident = SimpleNamespace(
        incident_id="inc-001",
        camera_id="cam-01",
        threat_level="HIGH",
        summary="Test incident",
        detected_at=datetime(2026, 1, 1, 10, 0, 0),
        status="OPEN",
    )

    q1 = _make_query(all=camera_rows)
    q2 = _make_query(scalar=142)  # detections_24h
    q3 = _make_query(scalar=11)   # threat_detections
    q4 = _make_query(scalar=51)   # total_incidents
    q5 = _make_query(scalar=7)    # open_incidents
    q6 = _make_query(scalar=13)   # incidents_24h
    q7 = _make_query(all=[("HIGH", 5), ("MEDIUM", 3), ("LOW", 5)])  # by threat
    q8 = _make_query(scalar=88)   # total_segments
    q9 = _make_query(scalar=1073741824)  # 1 GiB segment storage
    q10 = _make_query(first=latest_incident)

    session.query.side_effect = [q1, q2, q3, q4, q5, q6, q7, q8, q9, q10]
    return session


@pytest.fixture
def client(mock_db_session):
    app = FastAPI()
    app.include_router(stats_router)
    app.dependency_overrides[get_db] = lambda: mock_db_session
    tc = TestClient(app)
    yield tc
    tc.close()


@patch("api.routes.stats.bus")
@patch("api.routes.stats.shutil.disk_usage")
@patch("api.routes.stats.os.path.exists")
def test_get_stats_success(mock_exists, mock_disk_usage, mock_bus, client):
    mock_exists.return_value = True
    mock_disk_usage.return_value = SimpleNamespace(
        total=500 * 1024**3,
        used=120 * 1024**3,
        free=380 * 1024**3,
    )
    mock_bus.stats.return_value = {
        "raw_detections": 12,
        "vlm_requests": 5,
        "vlm_results": 3,
        "actions": 8,
    }

    response = client.get("/api/stats")
    assert response.status_code == 200

    data = response.json()

    # Current structured payload
    assert data["cameras"]["total"] == 2
    assert data["detections"]["last_24h"] == 142
    assert data["incidents"]["last_24h"] == 13
    assert data["recordings"]["total_segments"] == 88
    assert data["recordings"]["total_storage_gb"] == 1.0
    assert data["bus"]["queue_health"]["raw_detections"]["size"] == 12

    # Compatibility payload (used by dashboard/sidebar)
    assert len(data["camera_health"]) == 2
    assert data["detections_24h"] == 142
    assert data["incidents_24h"] == 13
    assert data["queues"] == {
        "raw_detections": 12,
        "vlm_requests": 5,
        "vlm_results": 3,
        "actions": 8,
    }
    assert data["disk_usage_bytes"] == 120 * 1024**3
    assert data["segments_storage_bytes"] == 1073741824


@patch("api.routes.stats.bus")
@patch("api.routes.stats.os.path.exists")
def test_stats_disk_usage_missing_dir(mock_exists, mock_bus, client):
    mock_exists.return_value = False
    mock_bus.stats.return_value = {
        "raw_detections": 0,
        "vlm_requests": 0,
        "vlm_results": 0,
        "actions": 0,
    }

    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()

    assert data["recordings"]["disk_usage"] is None
    assert data["disk_usage_bytes"] == 0
    assert data["queues"] == {
        "raw_detections": 0,
        "vlm_requests": 0,
        "vlm_results": 0,
        "actions": 0,
    }
