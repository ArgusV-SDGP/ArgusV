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


@pytest.fixture
def client():
    """Test client for FastAPI app."""
    return TestClient(app)


class TestMetricsEndpoint:
    """Test suite for /metrics Prometheus endpoint."""

    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_format(self, mock_bus, mock_get_db, client):
        """Test that metrics are in Prometheus text format."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.count.return_value = 0
        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"

        content = response.text

        # Check for Prometheus metric format
        assert "# HELP" in content
        assert "# TYPE" in content

        # Check for expected metrics
        assert "argusv_detections_total" in content
        assert "argusv_incidents_total" in content
        assert "argusv_queue_size" in content
        assert "argusv_camera_online" in content


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_with_cameras(self, mock_bus, mock_get_db, client):
        """Test metrics with multiple cameras."""
        # Setup mocks
        session = MagicMock()

        # Mock cameras
        camera1 = MagicMock(
            camera_id="cam-01",
            name="Front Gate",
            status="online",
        )
        camera2 = MagicMock(
            camera_id="cam-02",
            name="Parking Lot",
            status="offline",
        )

        session.query.return_value.all.return_value = [camera1, camera2]
        session.query.return_value.count.return_value = 100  # Detections
        session.query.return_value.filter.return_value.count.return_value = 5  # Incidents

        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 10
        mock_bus.vlm_requests.qsize.return_value = 8
        mock_bus.vlm_results.qsize.return_value = 6
        mock_bus.actions.qsize.return_value = 4
        mock_bus.alerts_ws.qsize.return_value = 2

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


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_detection_counter(self, mock_bus, mock_get_db, client):
        """Test detection counter metric."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.count.return_value = 1547  # Total detections
        session.query.return_value.filter.return_value.count.return_value = 0

        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_detections_total 1547" in content


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_incident_counter(self, mock_bus, mock_get_db, client):
        """Test incident counter metrics."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []

        # Mock incidents query chain
        mock_filter = MagicMock()
        session.query.return_value.filter.return_value = mock_filter

        # Total incidents
        session.query.return_value.count.return_value = 42

        # Open incidents
        mock_filter.count.return_value = 8

        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_incidents_total" in content
        assert "argusv_incidents_open" in content


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    @patch('os.path.exists')
    @patch('os.path.getsize')
    def test_metrics_disk_usage(self, mock_getsize, mock_exists, mock_bus, mock_get_db, client):
        """Test disk usage metric."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.count.return_value = 0

        mock_get_db.return_value = session

        mock_exists.return_value = True
        mock_getsize.return_value = 16846118912  # ~15.7 GB

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_disk_usage_bytes" in content
        assert "1.684611891" in content or "16846118912" in content


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_queue_health(self, mock_bus, mock_get_db, client):
        """Test all queue metrics are present."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.count.return_value = 0

        mock_get_db.return_value = session

        # Set different queue sizes
        mock_bus.raw_detections.qsize.return_value = 100
        mock_bus.vlm_requests.qsize.return_value = 75
        mock_bus.vlm_results.qsize.return_value = 50
        mock_bus.actions.qsize.return_value = 25
        mock_bus.alerts_ws.qsize.return_value = 10

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


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_segments_count(self, mock_bus, mock_get_db, client):
        """Test segments total metric."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.filter.return_value.count.return_value = 0

        # Mock segments count
        session.query.return_value.count.return_value = 250

        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        # Assertions
        assert response.status_code == 200
        content = response.text

        assert "argusv_segments_total 250" in content


    def test_metrics_no_auth_required(self, client):
        """Test that /metrics endpoint doesn't require authentication (for Prometheus scraping)."""
        # Make request without auth
        with patch('api.routes.metrics.get_db') as mock_get_db, \
             patch('api.routes.metrics.bus') as mock_bus:

            session = MagicMock()
            session.query.return_value.all.return_value = []
            session.query.return_value.count.return_value = 0
            session.query.return_value.filter.return_value.count.return_value = 0
            mock_get_db.return_value = session

            mock_bus.raw_detections.qsize.return_value = 0
            mock_bus.vlm_requests.qsize.return_value = 0
            mock_bus.vlm_results.qsize.return_value = 0
            mock_bus.actions.qsize.return_value = 0
            mock_bus.alerts_ws.qsize.return_value = 0

            response = client.get("/metrics")

        # Should succeed without auth
        assert response.status_code == 200


class TestMetricsPrometheus:
    """Test Prometheus scraping compatibility."""

    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_help_comments(self, mock_bus, mock_get_db, client):
        """Test that HELP comments are included."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.count.return_value = 0
        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        content = response.text

        # Check for HELP comments
        assert "# HELP argusv_detections_total" in content
        assert "# HELP argusv_incidents_total" in content
        assert "# HELP argusv_queue_size" in content


    @patch('api.routes.metrics.get_db')
    @patch('api.routes.metrics.bus')
    def test_metrics_type_declarations(self, mock_bus, mock_get_db, client):
        """Test that TYPE declarations are included."""
        # Setup mocks
        session = MagicMock()
        session.query.return_value.all.return_value = []
        session.query.return_value.count.return_value = 0
        session.query.return_value.filter.return_value.count.return_value = 0
        mock_get_db.return_value = session

        mock_bus.raw_detections.qsize.return_value = 0
        mock_bus.vlm_requests.qsize.return_value = 0
        mock_bus.vlm_results.qsize.return_value = 0
        mock_bus.actions.qsize.return_value = 0
        mock_bus.alerts_ws.qsize.return_value = 0

        # Make request
        response = client.get("/metrics")

        content = response.text

        # Check for TYPE declarations
        assert "# TYPE argusv_detections_total gauge" in content
        assert "# TYPE argusv_incidents_total gauge" in content
        assert "# TYPE argusv_queue_size gauge" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
