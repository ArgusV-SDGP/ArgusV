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


# ── Zone helpers ──────────────────────────────────────────────────────────────

_ZONE_ID      = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_VALID_POLY   = [[0.1, 0.1], [0.8, 0.1], [0.5, 0.9]]
_NEW_ZONE     = {"name": "Front Perimeter", "polygon_coords": _VALID_POLY}


def _make_zone_mock():
    from unittest.mock import MagicMock
    from datetime import datetime
    import uuid
    z = MagicMock()
    z.zone_id             = uuid.UUID(_ZONE_ID)
    z.name                = "Front Perimeter"
    z.polygon_coords      = _VALID_POLY
    z.zone_type           = "security"
    z.dwell_threshold_sec = 30
    z.active              = True
    z.created_at          = datetime(2026, 1, 1)
    return z


# ── GET /api/zones ────────────────────────────────────────────────────────────

def test_list_zones_returns_200():
    r = client.get("/api/zones")
    assert r.status_code == 200


def test_list_zones_returns_list():
    r = client.get("/api/zones")
    assert isinstance(r.json(), list)


def test_list_zones_returns_zone_objects(_override_deps):
    _override_deps.query.return_value.order_by.return_value.all.return_value = [_make_zone_mock()]
    body = client.get("/api/zones").json()
    assert len(body) == 1
    assert body[0]["name"]      == "Front Perimeter"
    assert body[0]["zone_type"] == "security"


# ── GET /api/zones/{zone_id} ──────────────────────────────────────────────────

def test_get_zone_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.get(f"/api/zones/{_ZONE_ID}")
    assert r.status_code == 404


def test_get_zone_returns_zone(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = _make_zone_mock()
    r = client.get(f"/api/zones/{_ZONE_ID}")
    assert r.status_code == 200
    assert r.json()["zone_id"] == _ZONE_ID


# ── POST /api/zones ───────────────────────────────────────────────────────────

def test_create_zone_returns_201(_override_deps):
    r = client.post("/api/zones", json=_NEW_ZONE)
    assert r.status_code == 201


def test_create_zone_returns_zone_data(_override_deps):
    body = client.post("/api/zones", json=_NEW_ZONE).json()
    assert body["name"]      == "Front Perimeter"
    assert "zone_id"         in body
    assert body["zone_type"] == "security"


def test_create_zone_rejects_invalid_polygon(_override_deps):
    # Self-intersecting polygon must return 400
    bad_poly = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
    r = client.post("/api/zones", json={"name": "Bad Zone", "polygon_coords": bad_poly})
    assert r.status_code == 400


# ── DELETE /api/zones/{zone_id} ───────────────────────────────────────────────

def test_delete_zone_returns_204(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = _make_zone_mock()
    r = client.delete(f"/api/zones/{_ZONE_ID}")
    assert r.status_code == 204


def test_delete_zone_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.delete(f"/api/zones/{_ZONE_ID}")
    assert r.status_code == 404


# ── Incident helpers ──────────────────────────────────────────────────────────

_INCIDENT_ID = "11111111-2222-3333-4444-555555555555"


def _make_incident_mock():
    from unittest.mock import MagicMock
    from datetime import datetime
    import uuid
    inc = MagicMock()
    inc.incident_id   = uuid.UUID(_INCIDENT_ID)
    inc.camera_id     = "cam-01"
    inc.zone_id       = None
    inc.zone_name     = "Front Gate"
    inc.object_class  = "person"
    inc.threat_level  = "HIGH"
    inc.summary       = "Suspicious loitering detected"
    inc.status        = "OPEN"
    inc.detected_at   = datetime(2026, 1, 1, 12, 0, 0)
    inc.resolved_at   = None
    inc.metadata_json = {}
    return inc


# ── GET /api/incidents (list — lives in server.py, uses get_db_sync directly) ─
# These endpoints call get_db_sync() inline, not via Depends(get_db),
# so we patch at the source rather than using dependency overrides.

def test_list_incidents_returns_200():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    with patch("db.connection.get_db_sync", return_value=db):
        r = client.get("/api/incidents")
    assert r.status_code == 200


def test_list_incidents_returns_list():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    with patch("db.connection.get_db_sync", return_value=db):
        assert isinstance(client.get("/api/incidents").json(), list)


def test_list_incidents_filters_by_camera():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value\
        .limit.return_value.all.return_value = []
    with patch("db.connection.get_db_sync", return_value=db):
        r = client.get("/api/incidents?camera_id=cam-01")
    assert r.status_code == 200


# ── GET /api/incidents/{incident_id} ─────────────────────────────────────────

def test_get_incident_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.get(f"/api/incidents/{_INCIDENT_ID}")
    assert r.status_code == 404


def test_get_incident_returns_incident(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = _make_incident_mock()
    r = client.get(f"/api/incidents/{_INCIDENT_ID}")
    assert r.status_code == 200
    body = r.json()
    assert body["incident_id"]  == _INCIDENT_ID
    assert body["threat_level"] == "HIGH"
    assert body["status"]       == "OPEN"


def test_get_incident_invalid_uuid_returns_400():
    r = client.get("/api/incidents/not-a-uuid")
    assert r.status_code == 400


# ── PATCH /api/incidents/{incident_id} ───────────────────────────────────────

def test_patch_incident_resolve(_override_deps):
    inc = _make_incident_mock()
    _override_deps.query.return_value.filter.return_value.first.return_value = inc
    r = client.patch(f"/api/incidents/{_INCIDENT_ID}", json={"status": "RESOLVED"})
    assert r.status_code == 200


def test_patch_incident_invalid_status(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = _make_incident_mock()
    r = client.patch(f"/api/incidents/{_INCIDENT_ID}", json={"status": "INVALID"})
    assert r.status_code == 400


def test_patch_incident_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.patch(f"/api/incidents/{_INCIDENT_ID}", json={"status": "RESOLVED"})
    assert r.status_code == 404


# ── GET /api/detections ───────────────────────────────────────────────────────

def test_list_detections_returns_200():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    with patch("db.connection.get_db_sync", return_value=db):
        r = client.get("/api/detections")
    assert r.status_code == 200


def test_list_detections_returns_list():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.query.return_value.order_by.return_value.limit.return_value.all.return_value = []
    with patch("db.connection.get_db_sync", return_value=db):
        assert isinstance(client.get("/api/detections").json(), list)


def test_list_detections_threats_only_filter():
    from unittest.mock import patch, MagicMock
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value\
        .limit.return_value.all.return_value = []
    with patch("db.connection.get_db_sync", return_value=db):
        r = client.get("/api/detections?threats_only=true")
    assert r.status_code == 200


# ── Segment helper ────────────────────────────────────────────────────────────

def _make_segment_mock():
    from unittest.mock import MagicMock
    from datetime import datetime
    import uuid
    seg = MagicMock()
    seg.segment_id      = uuid.uuid4()
    seg.camera_id       = "cam-01"
    seg.start_time      = datetime(2026, 1, 1, 12, 0, 0)
    seg.end_time        = datetime(2026, 1, 1, 12, 0, 10)
    seg.duration_sec    = 10.0
    seg.minio_path      = "/recordings/cam-01/seg-001.ts"
    seg.size_bytes      = 204800
    seg.has_motion      = True
    seg.has_detections  = True
    seg.detection_count = 2
    seg.locked          = False
    return seg


# ── GET /api/recordings/{camera_id} ──────────────────────────────────────────

def test_list_segments_returns_200(_override_deps):
    _override_deps.query.return_value.filter.return_value\
        .order_by.return_value.all.return_value = []
    r = client.get("/api/recordings/cam-01")
    assert r.status_code == 200


def test_list_segments_returns_list(_override_deps):
    _override_deps.query.return_value.filter.return_value\
        .order_by.return_value.all.return_value = []
    assert isinstance(client.get("/api/recordings/cam-01").json(), list)


def test_list_segments_returns_segment_data(_override_deps):
    seg = _make_segment_mock()
    _override_deps.query.return_value.filter.return_value\
        .order_by.return_value.all.return_value = [seg]
    body = client.get("/api/recordings/cam-01").json()
    assert len(body) == 1
    assert body[0]["camera_id"]    == "cam-01"
    assert body[0]["duration_sec"] == 10.0
    assert body[0]["minio_path"]   == "/recordings/cam-01/seg-001.ts"


# ── GET /api/recordings/{camera_id}/playlist ─────────────────────────────────

def test_playlist_no_segments_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value\
        .filter.return_value.filter.return_value\
        .order_by.return_value.all.return_value = []
    r = client.get(
        "/api/recordings/cam-01/playlist"
        "?start=2026-01-01T00:00:00&end=2026-01-01T01:00:00"
    )
    assert r.status_code == 404


def test_playlist_returns_m3u8(_override_deps):
    seg = _make_segment_mock()
    # playlist uses .filter(cond1, cond2, cond3) — single call, not chained
    _override_deps.query.return_value.filter.return_value\
        .order_by.return_value.all.return_value = [seg]
    r = client.get(
        "/api/recordings/cam-01/playlist"
        "?start=2026-01-01T00:00:00&end=2026-01-01T01:00:00"
    )
    assert r.status_code == 200
    assert "#EXTM3U" in r.text


# ── GET /api/incidents/{incident_id}/replay ───────────────────────────────────

def test_incident_replay_not_found_returns_404(_override_deps):
    _override_deps.query.return_value.filter.return_value.first.return_value = None
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/replay")
    assert r.status_code == 404


def test_incident_replay_returns_window(_override_deps):
    inc = _make_incident_mock()
    seg = _make_segment_mock()
    # first() returns the incident; subsequent query returns segments
    _override_deps.query.return_value.filter.return_value.first.return_value = inc
    _override_deps.query.return_value.filter.return_value\
        .filter.return_value.filter.return_value\
        .order_by.return_value.all.return_value = [seg]
    r = client.get(f"/api/incidents/{_INCIDENT_ID}/replay")
    assert r.status_code == 200
    body = r.json()
    assert "window"       in body
    assert "playlist_url" in body
    assert "segments"     in body
