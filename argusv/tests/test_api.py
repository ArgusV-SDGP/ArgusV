"""tests/test_api.py — Task TEST-02
API endpoint tests using FastAPI TestClient with mocked DB and auth.
No real Postgres, Redis, or background workers are started.
"""
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from api.server import app
from db.connection import get_db
from auth.jwt_handler import get_current_user

# ── Shared admin user returned by every overridden auth check ─────────────────

_ADMIN = {
    "auth_type": "jwt",
    "subject":   "test-admin",
    "username":  "test-admin",
    "role":      "ADMIN",
}

# ── TestClient (no lifespan — workers never start) ────────────────────────────

client = TestClient(app, raise_server_exceptions=False)


# ── Fixture: swap real DB and real auth for mocks on every test ───────────────

@pytest.fixture(autouse=True)
def _override_deps():
    """Override get_db and get_current_user for every test in this module."""
    db = MagicMock()
    # Preconfigure the most common query chains to return empty lists / None
    db.query.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

    app.dependency_overrides[get_db]           = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    yield db
    app.dependency_overrides.clear()


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_returns_200():
    r = client.get("/health")
    assert r.status_code == 200


def test_health_status_is_healthy():
    r = client.get("/health")
    assert r.json()["status"] == "healthy"


def test_health_response_has_required_keys():
    body = client.get("/health").json()
    assert "status"          in body
    assert "service"         in body
    assert "bus_queue_sizes" in body
    assert "uptime_sec"      in body


# ── /api/stats ────────────────────────────────────────────────────────────────

def test_stats_returns_200():
    r = client.get("/api/stats")
    assert r.status_code == 200


def test_stats_has_required_keys():
    body = client.get("/api/stats").json()
    for key in ("detections_total", "vlm_calls", "uptime_sec", "cpu_pct", "rss_mb"):
        assert key in body, f"missing key: {key}"


# ── /metrics (Prometheus) ─────────────────────────────────────────────────────

def test_metrics_returns_200():
    r = client.get("/metrics")
    assert r.status_code == 200


def test_metrics_content_type_is_plain_text():
    r = client.get("/metrics")
    assert "text/plain" in r.headers["content-type"]


def test_metrics_contains_prometheus_counters():
    text = client.get("/metrics").text
    assert "argusv_detections_total"  in text
    assert "argusv_vlm_calls_total"   in text
    assert "argusv_uptime_seconds"    in text
    assert "argusv_cpu_percent"       in text


# ── GET /api/cameras ──────────────────────────────────────────────────────────

def test_list_cameras_returns_200():
    r = client.get("/api/cameras")
    assert r.status_code == 200


def test_list_cameras_returns_list(_override_deps):
    _override_deps.query.return_value.order_by.return_value.all.return_value = []
    r = client.get("/api/cameras")
    assert isinstance(r.json(), list)


def test_list_cameras_returns_camera_objects(_override_deps):
    from datetime import datetime
    from unittest.mock import MagicMock
    cam = MagicMock()
    cam.camera_id  = "cam-01"
    cam.name       = "Front Gate"
    cam.rtsp_url   = "rtsp://localhost:8554/cam-01"
    cam.zone_id    = None
    cam.status     = "online"
    cam.resolution = "1920x1080"
    cam.fps        = 25
    cam.created_at = datetime(2026, 1, 1, 0, 0, 0)
    cam.last_seen  = datetime(2026, 1, 1, 0, 0, 0)

    _override_deps.query.return_value.order_by.return_value.all.return_value = [cam]
    r = client.get("/api/cameras")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["camera_id"] == "cam-01"
    assert body[0]["name"]      == "Front Gate"
    assert body[0]["status"]    == "online"


# ── GET /api/cameras/{camera_id} ──────────────────────────────────────────────

def test_get_camera_not_found(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.get("/api/cameras/cam-missing")
    assert r.status_code == 404


def test_get_camera_returns_camera(_override_deps):
    from datetime import datetime
    from unittest.mock import MagicMock
    cam = MagicMock()
    cam.camera_id  = "cam-02"
    cam.name       = "Parking Lot"
    cam.rtsp_url   = "rtsp://localhost:8554/cam-02"
    cam.zone_id    = None
    cam.status     = "online"
    cam.resolution = "1280x720"
    cam.fps        = 30
    cam.created_at = datetime(2026, 1, 1, 0, 0, 0)
    cam.last_seen  = datetime(2026, 1, 1, 0, 0, 0)

    _override_deps.query.return_value.filter.return_value.first.return_value = cam
    r = client.get("/api/cameras/cam-02")
    assert r.status_code == 200
    body = r.json()
    assert body["camera_id"] == "cam-02"
    assert body["name"]      == "Parking Lot"


# ── POST /api/cameras ─────────────────────────────────────────────────────────

_NEW_CAM = {
    "camera_id": "cam-03",
    "name":      "Back Door",
    "rtsp_url":  "rtsp://localhost:8554/cam-03",
}


def test_create_camera_returns_201(_override_deps):
    # No existing camera with that id
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.post("/api/cameras", json=_NEW_CAM)
    assert r.status_code == 201


def test_create_camera_returns_camera_data(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.post("/api/cameras", json=_NEW_CAM)
    body = r.json()
    assert body["camera_id"] == "cam-03"
    assert body["name"]      == "Back Door"


def test_create_camera_conflict_returns_409(_override_deps):
    from unittest.mock import MagicMock
    _override_deps.query.return_value.filter.return_value.first.return_value = MagicMock()
    r = client.post("/api/cameras", json=_NEW_CAM)
    assert r.status_code == 409


# ── PUT /api/cameras/{camera_id} ──────────────────────────────────────────────

def test_update_camera_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.put("/api/cameras/cam-missing", json={"name": "New Name"})
    assert r.status_code == 404


def test_update_camera_returns_updated_data(_override_deps):
    from datetime import datetime
    from unittest.mock import MagicMock
    cam = MagicMock()
    cam.camera_id  = "cam-03"
    cam.name       = "Updated Name"
    cam.rtsp_url   = "rtsp://localhost:8554/cam-03"
    cam.zone_id    = None
    cam.status     = "online"
    cam.resolution = None
    cam.fps        = 25
    cam.created_at = datetime(2026, 1, 1)
    cam.last_seen  = datetime(2026, 1, 1)

    _override_deps.query.return_value.filter.return_value.first.return_value = cam
    r = client.put("/api/cameras/cam-03", json={"name": "Updated Name"})
    assert r.status_code == 200
    assert r.json()["camera_id"] == "cam-03"


# ── DELETE /api/cameras/{camera_id} ───────────────────────────────────────────

def test_delete_camera_returns_204(_override_deps):
    from unittest.mock import MagicMock
    _override_deps.query.return_value.filter.return_value.first.return_value = MagicMock()
    r = client.delete("/api/cameras/cam-03")
    assert r.status_code == 204


def test_delete_camera_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.delete("/api/cameras/cam-missing")
    assert r.status_code == 404
