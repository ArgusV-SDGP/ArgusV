"""
tests/test_fixtures.py — Verify all conftest fixtures load and work correctly.
Commit #35 — TEST-01 final validation.
"""
from db.models import Camera, Zone, Segment, Incident, Detection, Rule


def test_db_session_creates_tables(db_session):
    """db_session fixture should provide a working SQLite session."""
    assert db_session is not None
    assert db_session.bind is not None


def test_sample_camera_persists(db_session, sample_camera):
    assert db_session.query(Camera).count() == 1
    assert sample_camera.camera_id == "cam-test-01"
    assert sample_camera.status == "online"


def test_sample_zone_persists(db_session, sample_zone):
    assert db_session.query(Zone).count() == 1
    assert sample_zone.name == "Test Zone"
    assert sample_zone.zone_type == "intrusion"


def test_sample_segment_links_to_camera(db_session, sample_segment, sample_camera):
    assert sample_segment.camera_id == sample_camera.camera_id
    assert db_session.query(Segment).count() == 1


def test_sample_incident_links_to_camera(db_session, sample_incident, sample_camera):
    assert sample_incident.camera_id == sample_camera.camera_id
    assert sample_incident.status == "OPEN"


def test_sample_detection_links_to_camera_and_incident(
    db_session, sample_detection, sample_camera, sample_incident
):
    assert sample_detection.camera_id == sample_camera.camera_id
    assert sample_detection.incident_id == sample_incident.incident_id
    assert sample_detection.confidence == 0.92


def test_sample_rule_links_to_zone(db_session, sample_rule, sample_zone):
    assert sample_rule.zone_id == sample_zone.zone_id
    assert sample_rule.severity == "HIGH"


def test_all_fixtures_load(
    sample_detection, sample_rule, mock_redis, mock_vlm, mock_bus
):
    """Smoke test — every fixture instantiates without error."""
    assert sample_detection is not None
    assert sample_rule is not None
    assert mock_redis is not None
    assert mock_vlm is not None
    assert mock_bus is not None


def test_mock_redis_interface(mock_redis):
    assert mock_redis.get("any_key") is None
    assert mock_redis.set("k", "v") is True


def test_mock_vlm_returns_dict(mock_vlm):
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(mock_vlm())
    assert result["is_threat"] is False
    assert "summary" in result
