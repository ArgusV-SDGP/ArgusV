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
