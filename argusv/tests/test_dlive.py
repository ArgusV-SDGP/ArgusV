"""tests/test_dlive.py — DLIVE-08 dashboard & monitoring tests"""
import pytest
import time
from unittest.mock import patch, MagicMock


# ── Stats emitter tests ──────────────────────────────────────────────────────

class TestStatsEmitter:
    def test_record_detection_increments(self):
        from stats import emitter
        before = emitter._stats["detections_total"]
        emitter.record_detection("cam-01")
        assert emitter._stats["detections_total"] == before + 1
        assert emitter._stats["detections_per_cam"]["cam-01"] >= 1

    def test_record_vlm_call(self):
        from stats import emitter
        before = emitter._stats["vlm_calls"]
        emitter.record_vlm_call(150.0)
        assert emitter._stats["vlm_calls"] == before + 1

    def test_record_alert(self):
        from stats import emitter
        before = emitter._stats["alerts_sent"]
        emitter.record_alert()
        assert emitter._stats["alerts_sent"] == before + 1

    def test_get_stats_shape(self):
        from stats.emitter import get_stats
        s = get_stats()
        assert "detections_total" in s
        assert "vlm_calls" in s
        assert "uptime_sec" in s
        assert "cpu_pct" in s
        assert "rss_mb" in s
        assert "disk" in s

    def test_set_bus_enables_queue_depths(self, mock_bus):
        from stats.emitter import set_bus, get_stats
        set_bus(mock_bus)
        s = get_stats()
        assert "queue_depths" in s
        assert "raw_detections" in s["queue_depths"]


# ── BirdsEyeRenderer tests ───────────────────────────────────────────────────

class TestBirdsEyeRenderer:
    def _make_renderer(self):
        from output.birdseye import BirdsEyeRenderer
        return BirdsEyeRenderer([
            {"camera_id": "cam-01", "name": "Front Gate"},
            {"camera_id": "cam-02", "name": "Parking"},
        ])

    def test_render_returns_jpeg(self):
        r = self._make_renderer()
        data = r.render()
        assert isinstance(data, bytes)
        assert len(data) > 0
        # JPEG magic bytes
        assert data[:2] == b'\xff\xd8'

    def test_update_and_render(self):
        r = self._make_renderer()
        r.update_object(1, 0.5, 0.5, "person", "HIGH")
        data = r.render()
        assert isinstance(data, bytes)
        assert data[:2] == b'\xff\xd8'

    def test_remove_object(self):
        r = self._make_renderer()
        r.update_object(42, 0.3, 0.7, "car", "LOW")
        r.remove_object(42)
        # Should still render without error
        data = r.render()
        assert data[:2] == b'\xff\xd8'

    def test_empty_cameras(self):
        from output.birdseye import BirdsEyeRenderer
        r = BirdsEyeRenderer([])
        data = r.render()
        assert data[:2] == b'\xff\xd8'


# ── Watchdog worker tests ────────────────────────────────────────────────────

class TestWatchdog:
    @pytest.mark.asyncio
    async def test_check_disk_runs_without_error(self):
        """_check_disk is async and logs — just ensure no crash."""
        from workers.watchdog_worker import _check_disk
        await _check_disk()  # should not raise

    @pytest.mark.asyncio
    async def test_check_queue_depth_runs(self):
        """_check_queue_depth reads from global bus — just ensure no crash."""
        from workers.watchdog_worker import _check_queue_depth
        await _check_queue_depth()


# ── Birdseye API endpoint test ────────────────────────────────────────────────

class TestBirdseyeEndpoint:
    def test_birdseye_returns_jpeg(self):
        """Test /api/birdseye returns image/jpeg when renderer is set."""
        from output.birdseye import BirdsEyeRenderer
        renderer = BirdsEyeRenderer([{"camera_id": "cam-1", "name": "Test"}])

        with patch("workers.pipeline_worker.birdseye_renderer", renderer):
            from fastapi.testclient import TestClient
            from api.server import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/birdseye")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/jpeg"
            assert resp.content[:2] == b'\xff\xd8'

    def test_birdseye_returns_503_when_no_renderer(self):
        """Test /api/birdseye returns 503 when renderer not initialised."""
        with patch("workers.pipeline_worker.birdseye_renderer", None):
            from fastapi.testclient import TestClient
            from api.server import app
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/birdseye")
            assert resp.status_code == 503
