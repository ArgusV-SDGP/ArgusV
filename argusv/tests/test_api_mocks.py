"""
tests/test_api_mocks.py — Mock tests for ArgusV APIs
-----------------------------------------------------
Task: TEST-05

Tests:
  - GET /health
  - GET /metrics
  - GET /api/birdseye
"""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Mock psutil to avoid issues in some environments
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

    @patch("stats.emitter.get_prometheus_metrics")
    def test_metrics_endpoint(self, mock_metrics):
        """Verify Prometheus metrics endpoint calls the formatter."""
        mock_metrics.return_value = "argusv_test_metric 123\n"
        response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("argusv_test_metric 123", response.text)

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
