"""Regression tests for ZoneMatcher zone-vs-full-frame behavior."""

import sys
from pathlib import Path

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers.edge_worker import ZoneMatcher


def _make_matcher() -> ZoneMatcher:
    matcher = ZoneMatcher.__new__(ZoneMatcher)
    matcher._zones = {}
    matcher._polygon_cache = {}
    matcher._camera_zone_map = {}
    matcher._stats = {
        "matched": 0,
        "outside_dropped": 0,
        "fallback_full_frame": 0,
        "hot_reload_events": 0,
        "db_reload_count": 0,
        "redis_reconnects": 0,
    }
    import threading
    matcher._lock = threading.Lock()
    return matcher


def test_outside_all_zones_returns_none_when_zones_exist():
    matcher = _make_matcher()
    matcher._zones["zone-1"] = {"id": "zone-1", "name": "Entrance", "allowed_classes": None}
    matcher._polygon_cache["zone-1"] = Polygon([[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.1, 0.3]])

    # Outside configured zone -> drop, do not synthesize full frame.
    assert matcher.match(0.9, 0.9, "person", camera_id="cam-1") is None


def test_specific_zone_prioritized_over_full_frame():
    matcher = _make_matcher()
    matcher._zones["full"] = {"id": "full", "name": "Full Frame", "allowed_classes": None}
    matcher._zones["zone-a"] = {"id": "zone-a", "name": "Gate", "allowed_classes": None}

    # Both polygons include the point; specific zone should win.
    matcher._polygon_cache["full"] = Polygon([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    matcher._polygon_cache["zone-a"] = Polygon([[0.2, 0.2], [0.7, 0.2], [0.7, 0.7], [0.2, 0.7]])

    result = matcher.match(0.5, 0.5, "person", camera_id="cam-1")
    assert result is not None
    assert result["id"] == "zone-a"


def test_falls_back_to_synthetic_full_frame_only_when_no_zones_configured():
    matcher = _make_matcher()
    result = matcher.match(0.5, 0.5, "person", camera_id="cam-1")
    assert result is not None
    assert result["id"] == "default"
    assert result["name"] == "Full Frame"

