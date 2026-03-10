"""tests/test_zones.py — Zone API validation helpers."""

from fastapi import HTTPException

from api.routes import zones


def test_validate_polygon_accepts_valid_triangle():
    polygon = [[0.1, 0.1], [0.8, 0.1], [0.5, 0.9]]
    normalized = zones._validate_polygon(polygon)
    assert normalized == polygon


def test_validate_polygon_removes_repeated_last_point():
    polygon = [[0.1, 0.1], [0.8, 0.1], [0.5, 0.9], [0.1, 0.1]]
    normalized = zones._validate_polygon(polygon)
    assert len(normalized) == 3
    assert normalized[0] != normalized[-1]


def test_validate_polygon_rejects_self_intersection():
    # Bowtie/butterfly shape — self-intersecting, rejected with 400
    invalid = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
    try:
        zones._validate_polygon(invalid)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "self-intersect" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException for self-intersecting polygon")


def test_publish_zone_update_created(monkeypatch):
    calls = []

    class MockRedis:
        def set(self, key, value):
            calls.append(("set", key, value))

        def publish(self, channel, payload):
            calls.append(("publish", channel, payload))

        def incr(self, key):
            calls.append(("incr", key))

        def delete(self, key):
            calls.append(("delete", key))

    monkeypatch.setattr(zones.redis, "from_url", lambda *_args, **_kwargs: MockRedis())
    zones._publish_zone_update(
        "zone-123",
        "created",
        {"zone_id": "zone-123", "name": "Front Gate", "polygon_coords": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2]]},
    )
    assert any(call[0] == "set" and call[1] == "config:zone:zone-123" for call in calls)
    assert any(call[0] == "incr" and call[1] == "config:zones:version" for call in calls)
    assert any(call[0] == "publish" and call[1] == "config-updates" for call in calls)

