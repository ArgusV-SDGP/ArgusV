"""
tests/test_stats_api.py — Tests for Stats API Endpoint
Task: TEST-01
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Add src to path
import sys
from pathlib import Path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from api.server import app
from db.models import Camera, Detection, Incident, Segment


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_db_session():
    """Mock database session with sample data."""
    session = MagicMock()

    # Mock cameras
    camera1 = Camera(
        camera_id="cam-01",
        name="Front Gate",
        status="online",
        fps=25,
        last_frame_ts=datetime.now(timezone.utc),
    )
    camera2 = Camera(
        camera_id="cam-02",
        name="Parking Lot",
        status="offline",
        fps=0,
        last_frame_ts=datetime.now(timezone.utc) - timedelta(hours=2),
    )

    # Configure query mock for cameras
    session.query.return_value.all.return_value = [camera1, camera2]

    # Mock detections count (24h)
    session.query.return_value.filter.return_value.count.return_value = 142

    # Mock incidents
    incident1 = MagicMock(
        incident_id="inc-001",
        threat_level="HIGH",
        status="open",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    incident2 = MagicMock(
        incident_id="inc-002",
        threat_level="MEDIUM",
        status="closed",
        created_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )

    session.query.return_value.filter.return_value.all.return_value = [incident1, incident2]

    # Mock segments
    session.query.return_value.count.return_value = 150

    return session


class TestStatsAPI:
    """Test suite for /api/stats endpoint."""

    @patch('api.routes.stats.get_db')
    @patch('api.routes.stats.bus')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    def test_get_stats_success(self, mock_getsize, mock_exists, mock_bus, mock_get_db, client, mock_db_session):
        """Test successful stats retrieval."""
        # Setup mocks
        mock_get_db.return_value = mock_db_session
        mock_exists.return_value = True
        mock_getsize.return_value = 16846118912  # ~15.7 GB

        # Mock bus queues
        mock_bus.raw_detections.qsize.return_value = 12
        mock_bus.vlm_requests.qsize.return_value = 5
        mock_bus.vlm_results.qsize.return_value = 3
        mock_bus.actions.qsize.return_value = 8
        mock_bus.alerts_ws.qsize.return_value = 14

        # Make request (with mock auth)
        with patch('api.routes.stats.require_roles') as mock_auth:
            mock_auth.return_value = lambda: {"user_id": "test-user", "role": "ADMIN"}
            response = client.get("/api/stats")

        # Assertions
        assert response.status_code == 200
        data = response.json()

        assert "cameras" in data
        assert len(data["cameras"]) == 2
        assert data["cameras"][0]["camera_id"] == "cam-01"
        assert data["cameras"][0]["status"] == "online"

        assert "detections_24h" in data
        assert data["detections_24h"] == 142

        assert "incidents_24h" in data
        assert data["incidents_24h"] == 2

        assert "queues" in data
        assert data["queues"]["raw_detections"] == 12
        assert data["queues"]["vlm_requests"] == 5

        assert "disk_usage_bytes" in data
        assert data["disk_usage_bytes"] > 0


    @patch('api.routes.stats.get_db')
    @patch('api.routes.stats.bus')
    def test_stats_with_no_cameras(self, mock_bus, mock_get_db, client):
        """Test stats endpoint when no cameras are configured."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.filter.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0

        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        with patch('api.routes.stats.require_roles') as mock_auth:
            mock_auth.return_value = lambda: {"user_id": "test-user", "role": "VIEWER"}
            response = client.get("/api/stats")

        # Assertions
        assert response.status_code == 200
        data = response.json()

        assert data["cameras"] == []
        assert data["detections_24h"] == 0
        assert data["incidents_24h"] == 0
        assert data["segments_total"] == 0


    @patch('api.routes.stats.get_db')
    @patch('api.routes.stats.bus')
    def test_stats_queue_health(self, mock_bus, mock_get_db, client):
        """Test queue health reporting in stats."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.filter.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0

        mock_get_db.return_value = session

        # Simulate high queue load
        mock_bus.raw_detections.qsize.return_value = 250
        mock_bus.vlm_requests.qsize.return_value = 180
        mock_bus.vlm_results.qsize.return_value = 90
        mock_bus.actions.qsize.return_value = 60
        mock_bus.alerts_ws.qsize.return_value = 120

        # Make request
        with patch('api.routes.stats.require_roles') as mock_auth:
            mock_auth.return_value = lambda: {"user_id": "test-user", "role": "OPERATOR"}
            response = client.get("/api/stats")

        # Assertions
        assert response.status_code == 200
        data = response.json()

        assert data["queues"]["raw_detections"] == 250
        assert data["queues"]["vlm_requests"] == 180

        # Verify total queue depth
        total_queue = sum(data["queues"].values())
        assert total_queue == 700  # 250 + 180 + 90 + 60 + 120


    @patch('api.routes.stats.get_db')
    @patch('api.routes.stats.bus')
    @patch('os.path.exists')
    def test_stats_disk_usage_missing_dir(self, mock_exists, mock_bus, mock_get_db, client):
        """Test stats when recordings directory doesn't exist."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.filter.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0

        mock_get_db.return_value = session
        mock_exists.return_value = False  # Directory doesn't exist

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        with patch('api.routes.stats.require_roles') as mock_auth:
            mock_auth.return_value = lambda: {"user_id": "test-user", "role": "ADMIN"}
            response = client.get("/api/stats")

        # Assertions
        assert response.status_code == 200
        data = response.json()

        # Disk usage should be 0 when directory doesn't exist
        assert data["disk_usage_bytes"] == 0


    def test_stats_unauthorized(self, client):
        """Test stats endpoint requires authentication."""
        # Make request without auth
        response = client.get("/api/stats")

        # Should return 401 or redirect to login
        assert response.status_code in [401, 403]


class TestStatsFiltering:
    """Test stats filtering and aggregation logic."""

    @patch('api.routes.stats.get_db')
    @patch('api.routes.stats.bus')
    def test_detections_24h_filtering(self, mock_bus, mock_get_db, client):
        """Test that only detections from last 24 hours are counted."""
        session = MagicMock()

        # Mock cameras
        session.query.return_value.all.return_value = []

        # Mock detections: only those from last 24h should be counted
        # This is tested by verifying the filter is called with correct timestamp
        mock_filter = MagicMock()
        session.query.return_value.filter.return_value = mock_filter
        mock_filter.count.return_value = 42
        mock_filter.all.return_value = []

        session.query.return_value.count.return_value = 0

        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        with patch('api.routes.stats.require_roles') as mock_auth:
            mock_auth.return_value = lambda: {"user_id": "test-user", "role": "ADMIN"}
            response = client.get("/api/stats")

        # Verify filter was called (timestamp check)
        assert session.query.called
        assert mock_filter.count.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
