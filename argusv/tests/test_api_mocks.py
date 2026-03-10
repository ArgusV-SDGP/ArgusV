"""
tests/test_api_mocks.py — Mock tests for ArgusV APIs
-----------------------------------------------------
Task: TEST-05

Tests:
  - GET /health
  - GET /metrics
  - GET /api/stats
  - GET /api/birdseye
"""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Mock stats before importing app to avoid psutil errors in restricted environments
with patch("psutil.Process"), patch("psutil.disk_usage"):
    from api.server import app

client = TestClient(app)

class TestReliabilityAPI(unittest.TestCase):

    def test_health_endpoint(self):
        """Verify the health endpoint returns expected structure."""
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("cameras", data)
        self.assertIn("uptime_sec", data)

    @patch("stats.emitter.get_stats")
    def test_metrics_endpoint(self, mock_get_stats):
        """Verify Prometheus metrics formatting."""
        mock_get_stats.return_value = {
            "cpu_pct": 5.0,
            "rss_mb": 150.0,
            "detections_total": 10,
            "vlm_calls": 2,
            "uptime_sec": 3600.0,
            "detections_per_cam": {"cam-01": 10}
        }
        response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("argusv_cpu_pct 5.0", response.text)
        self.assertIn('argusv_camera_detections_total{camera_id="cam-01"} 10', response.text)

    @patch("output.birdseye.get_birdseye_composite")
    def test_birdseye_endpoint(self, mock_get_birdseye):
        """Verify birdseye endpoint returns an image response."""
        mock_get_birdseye.return_value = b"fake-jpeg-data"
        response = client.get("/api/birdseye")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.content, b"fake-jpeg-data")

if __name__ == "__main__":
    unittest.main()
