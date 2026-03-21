"""tests/test_camera_zone_payload.py — camera-zone bbox payload helpers."""

from api.routes import cameras


def test_parse_resolution_valid():
    w, h = cameras._parse_resolution("1280x720")
    assert w == 1280
    assert h == 720


def test_parse_resolution_invalid():
    w, h = cameras._parse_resolution("not-a-resolution")
    assert w is None
    assert h is None


def test_compute_bbox_norm():
    bbox = cameras._compute_bbox([[0.1, 0.2], [0.4, 0.2], [0.4, 0.5], [0.1, 0.5]])
    assert bbox is not None
    assert bbox["x1"] == 0.1
    assert bbox["y1"] == 0.2
    assert bbox["x2"] == 0.4
    assert bbox["y2"] == 0.5
    assert bbox["w"] == 0.3
    assert bbox["h"] == 0.3


def test_bbox_to_px():
    bbox_px = cameras._bbox_to_px(
        {"x1": 0.1, "y1": 0.2, "x2": 0.4, "y2": 0.5, "w": 0.3, "h": 0.3, "cx": 0.25, "cy": 0.35, "area": 0.09},
        1280,
        720,
    )
    assert bbox_px is not None
    assert bbox_px["x1"] == 128
    assert bbox_px["y1"] == 144
    assert bbox_px["x2"] == 512
    assert bbox_px["y2"] == 360
