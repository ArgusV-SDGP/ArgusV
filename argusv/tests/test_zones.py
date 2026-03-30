"""tests/test_zones.py — Zone API validation helpers."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException
from pydantic import ValidationError

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


def test_zone_create_normalizes_name_and_allowed_classes():
    payload = zones.ZoneCreate(
        camera_id="cam-01",
        name="  Front Gate  ",
        polygon_coords=[[0.1, 0.1], [0.6, 0.1], [0.6, 0.7]],
        allowed_classes=[" Person ", "person", " CAR  ", ""],
    )
    assert payload.name == "Front Gate"
    assert payload.allowed_classes == ["person", "car"]


def test_rule_create_rejects_empty_object_classes():
    try:
        zones.RuleCreate(object_classes=[" ", ""])
    except ValidationError as exc:
        assert "object_classes" in str(exc)
    else:
        raise AssertionError("Expected ValidationError for empty object_classes")


def test_delete_all_zones_deletes_and_publishes(monkeypatch):
    deleted_events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        zones,
        "_publish_zone_update",
        lambda zone_id, action, zone_data=None: deleted_events.append((zone_id, action)),
    )

    zone_1 = SimpleNamespace(zone_id=uuid.uuid4())
    zone_2 = SimpleNamespace(zone_id=uuid.uuid4())

    db = MagicMock()
    zone_query = MagicMock()
    zone_query.all.return_value = [zone_1, zone_2]
    camera_query = MagicMock()
    camera_filter = MagicMock()
    camera_query.filter.return_value = camera_filter
    incident_query = MagicMock()
    incident_filter = MagicMock()
    incident_query.filter.return_value = incident_filter
    rule_query = MagicMock()
    rule_filter = MagicMock()
    rule_query.filter.return_value = rule_filter

    db.query.side_effect = [zone_query, camera_query, incident_query, rule_query]

    result = zones.delete_all_zones(db=db, _user={"role": "ADMIN"})

    assert result["deleted"] == 2
    assert set(result["zone_ids"]) == {str(zone_1.zone_id), str(zone_2.zone_id)}
    camera_filter.update.assert_called_once_with({"zone_id": None}, synchronize_session=False)
    incident_filter.update.assert_called_once_with({"zone_id": None}, synchronize_session=False)
    rule_filter.delete.assert_called_once_with(synchronize_session=False)
    assert db.delete.call_count == 2
    db.commit.assert_called_once()
    assert set(deleted_events) == {
        (str(zone_1.zone_id), "deleted"),
        (str(zone_2.zone_id), "deleted"),
    }


def test_delete_all_zones_when_empty(monkeypatch):
    deleted_events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        zones,
        "_publish_zone_update",
        lambda zone_id, action, zone_data=None: deleted_events.append((zone_id, action)),
    )

    db = MagicMock()
    zone_query = MagicMock()
    zone_query.all.return_value = []
    db.query.return_value = zone_query

    result = zones.delete_all_zones(db=db, _user={"role": "ADMIN"})

    assert result == {"deleted": 0, "zone_ids": []}
    db.commit.assert_not_called()
    assert deleted_events == []

