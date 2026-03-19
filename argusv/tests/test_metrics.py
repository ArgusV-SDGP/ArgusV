"""
tests/test_metrics.py — Tests for Prometheus Metrics Endpoint
Task: TEST-01
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Add src to path
import sys
from pathlib import Path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from api.server import app
from db.models import Camera, Detection, Incident
from db.connection import get_db

@pytest.fixture
def mock_db_session():
    """Mock database session."""
    return MagicMock()

@pytest.fixture
def client(mock_db_session):
    """Test client for FastAPI app."""
    app.dependency_overrides[get_db] = lambda: mock_db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestMetricsEndpoint:
    """Test suite for /metrics Prometheus endpoint."""

    @patch('api.routes.metrics.bus')
    def test_metrics_format(self, mock_bus, client, mock_db_session):
        """Test that metrics are in Prometheus text format."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.scalar.return_value = 0
        session.query.return_value.filter.return_value.scalar.return_value = 0

        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

        content = response.text

        # Check for Prometheus metric format
        assert "# HELP" in content
        assert "# TYPE" in content

        # Check for expected metrics
        assert "argusv_detections_total" in content
        assert "argusv_incidents_total" in content
        assert "argusv_queue_size" in content
        assert "argusv_camera_online" in content


    @patch('api.routes.metrics.bus')
    def test_metrics_with_cameras(self, mock_bus, client, mock_db_session):
        """Test metrics with multiple cameras."""
        # Setup mocks
        session = mock_db_session

        # Mock cameras
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        camera2 = MagicMock(camera_id="cam-02", status="offline")
        camera2.name = "Parking Lot"

        session.query.return_value.all.return_value = [camera1, camera2]
        session.query.return_value.scalar.return_value = 100  # Detections
        session.query.return_value.filter.return_value.scalar.return_value = 5  # Incidents


        mock_bus.stats.return_value = {"raw_detections": 10, "vlm_requests": 8, "vlm_results": 6, "actions": 4, "alerts_ws": 2}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        # Check camera metrics
        assert 'argusv_camera_online{camera_id="cam-01",name="Front Gate"} 1.0' in content
        assert 'argusv_camera_online{camera_id="cam-02",name="Parking Lot"} 0.0' in content

        # Check queue metrics
        assert 'argusv_queue_size{queue="raw_detections"} 10' in content
        assert 'argusv_queue_size{queue="vlm_requests"} 8' in content


    @patch('api.routes.metrics.bus')
    def test_metrics_detection_counter(self, mock_bus, client, mock_db_session):
        """Test detection counter metric."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.scalar.return_value = 1547  # Total detections
        session.query.return_value.filter.return_value.scalar.return_value = 0


        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_detections_total 1547" in content


    @patch('api.routes.metrics.bus')
    def test_metrics_incident_counter(self, mock_bus, client, mock_db_session):
        """Test incident counter metrics."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]

        # Mock incidents query chain
        mock_filter = MagicMock()
        session.query.return_value.filter.return_value = mock_filter

        # Total incidents
        session.query.return_value.scalar.return_value = 42

        # Open incidents
        mock_filter.count.return_value = 8


        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_incidents_total" in content
        assert "argusv_incidents_open" in content


    @patch('api.routes.metrics.bus')
    @patch('os.path.exists')
    @patch('shutil.disk_usage')
    def test_metrics_disk_usage(self, mock_disk_usage, mock_exists, mock_bus, client, mock_db_session):
        """Test disk usage metric."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.scalar.return_value = 0
        session.query.return_value.filter.return_value.scalar.return_value = 0


        mock_exists.return_value = True
        Usage = type('Usage', (), {'total': 16846118912.0, 'used': 1.0, 'free': 1.0})
        mock_disk_usage.return_value = Usage()

        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_disk_usage_bytes" in content
        assert "16846118912.0" in content


    @patch('api.routes.metrics.bus')
    def test_metrics_queue_health(self, mock_bus, client, mock_db_session):
        """Test all queue metrics are present."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.scalar.return_value = 0
        session.query.return_value.filter.return_value.scalar.return_value = 0


        # Set different queue sizes
        mock_bus.stats.return_value = {"raw_detections": 100, "vlm_requests": 75, "vlm_results": 50, "actions": 25, "alerts_ws": 10}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        # Verify all queues are reported
        assert 'argusv_queue_size{queue="raw_detections"} 100' in content
        assert 'argusv_queue_size{queue="vlm_requests"} 75' in content
        assert 'argusv_queue_size{queue="vlm_results"} 50' in content
        assert 'argusv_queue_size{queue="actions"} 25' in content
        assert 'argusv_queue_size{queue="alerts_ws"} 10' in content


    @patch('api.routes.metrics.bus')
    def test_metrics_segments_count(self, mock_bus, client, mock_db_session):
        """Test segments total metric."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.filter.return_value.scalar.return_value = 0

        # Mock segments count
        session.query.return_value.scalar.return_value = 250


        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_segments_total 250" in content


    def test_metrics_no_auth_required(self, client, mock_db_session):
        """Test that /metrics endpoint doesn't require authentication (for Prometheus scraping)."""
        # Make request without auth
        with patch('api.routes.metrics.bus') as mock_bus:

            session = mock_db_session
            camera1 = MagicMock(camera_id="cam-01", status="online")
            camera1.name = "Front Gate"
            session.query.return_value.all.return_value = [camera1]
            session.query.return_value.scalar.return_value = 0
            session.query.return_value.filter.return_value.scalar.return_value = 0

            mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

            response = client.get("/metrics")

        # Should succeed without auth
        assert response.status_code == 200


class TestMetricsPrometheus:
    """Test Prometheus scraping compatibility."""

    @patch('api.routes.metrics.bus')
    def test_metrics_help_comments(self, mock_bus, client, mock_db_session):
        """Test that HELP comments are included."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.scalar.return_value = 0
        session.query.return_value.filter.return_value.scalar.return_value = 0

        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        content = response.text

        # Check for HELP comments
        assert "# HELP argusv_detections_total" in content
        assert "# HELP argusv_incidents_total" in content
        assert "# HELP argusv_queue_size" in content


    @patch('api.routes.metrics.bus')
    def test_metrics_type_declarations(self, mock_bus, client, mock_db_session):
        """Test that TYPE declarations are included."""
        # Setup mocks
        session = mock_db_session
        camera1 = MagicMock(camera_id="cam-01", status="online")
        camera1.name = "Front Gate"
        session.query.return_value.all.return_value = [camera1]
        session.query.return_value.scalar.return_value = 0
        session.query.return_value.filter.return_value.scalar.return_value = 0

        mock_bus.stats.return_value = {"raw_detections": 0, "vlm_requests": 0, "vlm_results": 0, "actions": 0, "alerts_ws": 0}

        # Make request
        response = client.get("/metrics")

        content = response.text

        # Check for TYPE declarations
        assert "# TYPE argusv_detections_total gauge" in content
        assert "# TYPE argusv_incidents_total gauge" in content
        assert "# TYPE argusv_queue_size gauge" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
