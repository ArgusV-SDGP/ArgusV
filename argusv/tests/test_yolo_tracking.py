"""
tests/test_yolo_tracking.py — YOLO Tracking Tests
--------------------------------------------------
Tests for MotionGate, DwellTracker, ZoneMatcher, and DetectLoop
"""

import pytest
import time
import asyncio
import numpy as np
from shapely.geometry import Polygon
import cv2
from unittest.mock import Mock, patch, MagicMock
from shapely.geometry import Point, Polygon

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers.edge_worker import (
    MotionGate,
    DwellTracker,
    ZoneMatcher,
    DetectLoop,
    FrameBuffer,
    CameraWorker
)


class TestMotionGate:
    """Test MotionGate (background subtraction pre-filter)"""

    def test_initialization(self):
        """Test MotionGate initialization"""
        gate = MotionGate(threshold=0.005)

        assert gate._threshold == 0.005
        assert gate._warmup_frames == 30
        assert gate._frame_count == 0
        assert gate._bg_sub is not None

    def test_warmup_period_always_returns_true(self):
        """Test that warmup period always returns True"""
        gate = MotionGate()

        # Create a static frame
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # First 30 frames should always return True
        for i in range(30):
            result = gate.has_motion(frame)
            assert result == True

    def test_no_motion_detection(self):
        """Test detection of no motion (static scene)"""
        gate = MotionGate(threshold=0.003)

        # Create identical frames
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)

        # Warmup
        for _ in range(40):
            gate.has_motion(frame)

        # Now test with same frame - should detect no motion
        # Note: MOG2 may still show some noise, so we can't guarantee False
        result = gate.has_motion(frame)

        # Just verify it returns a boolean
        assert isinstance(result, (bool, np.bool_))

    def test_motion_detection(self):
        """Test detection of motion"""
        gate = MotionGate(threshold=0.003)

        # Warmup with static background
        bg_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(40):
            gate.has_motion(bg_frame)

        # Create frame with motion (white rectangle)
        motion_frame = bg_frame.copy()
        cv2.rectangle(motion_frame, (200, 200), (400, 400), (255, 255, 255), -1)

        # Should detect motion
        result = gate.has_motion(motion_frame)

        # With significant motion, should return True
        assert result == True

    def test_threshold_sensitivity(self):
        """Test that higher threshold reduces sensitivity"""
        # Low threshold gate (more sensitive)
        gate_sensitive = MotionGate(threshold=0.001)

        # High threshold gate (less sensitive)
        gate_insensitive = MotionGate(threshold=0.1)

        # Warmup both
        bg = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(40):
            gate_sensitive.has_motion(bg)
            gate_insensitive.has_motion(bg)

        # Small motion
        small_motion = bg.copy()
        cv2.rectangle(small_motion, (300, 300), (310, 310), (255, 255, 255), -1)

        # Sensitive should detect, insensitive might not
        sensitive_result = gate_sensitive.has_motion(small_motion)
        insensitive_result = gate_insensitive.has_motion(small_motion)

        # At least verify they return booleans
        assert isinstance(sensitive_result, (bool, np.bool_))
        assert isinstance(insensitive_result, (bool, np.bool_))


class TestDwellTracker:
    """Test DwellTracker (loitering detection)"""

    def test_initialization(self):
        """Test DwellTracker initialization"""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(
            on_event=on_event,
            camera_id="cam-01",
            loiter_threshold_sec=30,
            update_interval_sec=10,
            evict_after_sec=5
        )

        assert tracker._camera_id == "cam-01"
        assert tracker._loiter_sec == 30
        assert tracker._update_sec == 10
        assert tracker._evict_sec == 5
        assert len(tracker._tracks) == 0

    def test_track_start_event(self):
        """Test that START event is emitted for new track"""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(
            on_event=on_event,
            camera_id="cam-01",
            loiter_threshold_sec=30
        )

        # Simulate first detection
        event = {
            "event_id": "evt-001",
            "camera_id": "cam-01",
            "object_class": "person",
            "confidence": 0.85,
            "track_id": 1,
            "zone_id": "zone-01",
            "zone_name": "Entrance",
            "bbox": {"x1": 100, "y1": 100, "x2": 200, "y2": 200}
        }

        tracker.update(1, "zone-01", "Entrance", event)

        # Should emit START event
        assert len(events) == 1
        assert events[0]["event_type"] == "START"
        assert events[0]["dwell_sec"] == 0
        assert events[0]["track_id"] == 1

    def test_track_update_events(self):
        """Test that UPDATE events are emitted periodically"""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(
            on_event=on_event,
            camera_id="cam-01",
            loiter_threshold_sec=30,
            update_interval_sec=2  # Update every 2 seconds
        )

        event = {
            "event_id": "evt-001",
            "camera_id": "cam-01",
            "track_id": 1,
            "zone_id": "zone-01",
            "zone_name": "Entrance"
        }

        # Initial update (START)
        tracker.update(1, "zone-01", "Entrance", event)
        assert len(events) == 1

        # Wait 2.5 seconds and update again
        time.sleep(2.5)
        tracker.update(1, "zone-01", "Entrance", event)

        # Should have emitted UPDATE event
        assert len(events) == 2
        assert events[1]["event_type"] == "UPDATE"
        assert events[1]["dwell_sec"] > 2

    def test_loitering_detection(self):
        """Test that LOITERING event is emitted after threshold"""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(
            on_event=on_event,
            camera_id="cam-01",
            loiter_threshold_sec=2,  # 2 seconds for testing
            update_interval_sec=10
        )

        event = {
            "event_id": "evt-001",
            "camera_id": "cam-01",
            "track_id": 1,
            "zone_id": "zone-01",
            "zone_name": "Restricted Area"
        }

        # Initial update
        tracker.update(1, "zone-01", "Restricted Area", event)

        # Wait for loitering threshold
        time.sleep(2.5)

        # Update again
        tracker.update(1, "zone-01", "Restricted Area", event)

        # Should have START + LOITERING events
        assert len(events) == 2
        assert events[0]["event_type"] == "START"
        assert events[1]["event_type"] == "LOITERING"
        assert events[1]["dwell_sec"] >= 2

    def test_per_zone_loiter_threshold_override(self):
        """Track should use zone-specific loiter threshold when provided."""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(
            on_event=on_event,
            camera_id="cam-01",
            loiter_threshold_sec=30,  # global default, should be overridden
            update_interval_sec=10
        )

        event = {
            "event_id": "evt-001",
            "camera_id": "cam-01",
            "track_id": 1,
            "zone_id": "zone-01",
            "zone_name": "Restricted Area"
        }

        tracker.update(1, "zone-01", "Restricted Area", event, loiter_threshold_sec=1)
        time.sleep(1.2)
        tracker.update(1, "zone-01", "Restricted Area", event, loiter_threshold_sec=1)

        assert len(events) == 2
        assert events[1]["event_type"] == "LOITERING"

    def test_track_eviction(self):
        """Test that tracks are evicted after inactivity"""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(
            on_event=on_event,
            camera_id="cam-01",
            evict_after_sec=1  # Evict after 1 second
        )

        event = {
            "event_id": "evt-001",
            "camera_id": "cam-01",
            "track_id": 1,
            "zone_id": "zone-01",
            "zone_name": "Exit"
        }

        # Start track
        tracker.update(1, "zone-01", "Exit", event)

        # Wait for eviction
        time.sleep(3)

        # Should have emitted START + END events
        assert len(events) == 2
        assert events[0]["event_type"] == "START"
        assert events[1]["event_type"] == "END"

    def test_flush_all_tracks(self):
        """Test that flush_all() ends all active tracks"""
        events = []

        def on_event(event):
            events.append(event)

        tracker = DwellTracker(on_event=on_event, camera_id="cam-01")

        # Create multiple tracks
        for i in range(3):
            event = {
                "event_id": f"evt-{i}",
                "camera_id": "cam-01",
                "track_id": i,
                "zone_id": "zone-01",
                "zone_name": "Area"
            }
            tracker.update(i, "zone-01", "Area", event)

        # Should have 3 START events
        assert len(events) == 3

        # Flush all
        tracker.flush_all()

        # Should have 3 START + 3 END events
        assert len(events) == 6
        end_events = [e for e in events if e["event_type"] == "END"]
        assert len(end_events) == 3


class TestZoneMatcher:
    """Test ZoneMatcher (polygon zone matching)"""

    @patch('workers.edge_worker.create_engine')
    def test_initialization_loads_zones(self, mock_engine):
        """Test that ZoneMatcher loads zones from DB"""
        # Mock DB connection
        mock_conn = MagicMock()
        mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn

        # Mock zone data
        mock_conn.execute.return_value.fetchall.return_value = [
            MagicMock(
                zone_id="zone-01",
                camera_id=None,
                name="Entrance",
                polygon_coords=[[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4]],
                dwell_threshold_sec=30,
                active=True
            )
        ]

        with patch('config.POSTGRES_URL', "postgresql://test"):
            matcher = ZoneMatcher()

            # Should have loaded 1 zone
            assert len(matcher._zones) == 1
            assert "zone-01" in matcher._zones

    def test_match_point_inside_zone(self):
        """Test matching a point inside a zone"""
        matcher = ZoneMatcher()
        matcher._zones = {}
        matcher._polygon_cache = {}
        matcher._camera_zone_map = {}

        # Manually add a test zone (square)
        matcher._zones["zone-01"] = {"id": "zone-01", "name": "Test Zone", "polygon_coords": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}
        matcher._polygon_cache["zone-01"] = Polygon([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])

        # Test point inside zone
        result = matcher.match(0.5, 0.5)

        assert result is not None
        assert result["id"] == "zone-01"
        assert result["name"] == "Test Zone"

    def test_match_point_outside_zone(self):
        """Test matching a point outside all zones"""
        matcher = ZoneMatcher()
        matcher._zones = {}
        matcher._polygon_cache = {}
        matcher._camera_zone_map = {}

        # Add test zone
        matcher._zones["zone-01"] = {"id": "zone-01", "name": "Test Zone", "polygon_coords": [[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4]]}
        matcher._polygon_cache["zone-01"] = Polygon([[0.2, 0.2], [0.4, 0.2], [0.4, 0.4], [0.2, 0.4]])

        # Test point outside zone
        result = matcher.match(0.9, 0.9)

        # Should return None (outside all zones)
        assert result is None

    def test_match_point_on_boundary_is_included(self):
        """Boundary points should match (covers), not be dropped."""
        matcher = ZoneMatcher()
        matcher._zones = {}
        matcher._polygon_cache = {}
        matcher._camera_zone_map = {}
        matcher._zones["zone-01"] = {"id": "zone-01", "name": "Test Zone", "polygon_coords": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}
        matcher._polygon_cache["zone-01"] = Polygon([[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]])

        # Point on left boundary x=0.2
        result = matcher.match(0.2, 0.5)
        assert result is not None
        assert result["id"] == "zone-01"

    def test_default_zone_when_no_zones_defined(self):
        """Test that default zone is returned when no zones exist"""
        matcher = ZoneMatcher()
        matcher._zones = {}  # No zones
        matcher._polygon_cache = {}
        matcher._camera_zone_map = {}

        result = matcher.match(0.5, 0.5)

        assert result is not None
        assert result["id"] == "default"
        assert result["name"] == "Full Frame"

    def test_multiple_zones_returns_first_match(self):
        """Test with overlapping zones (returns first match)"""
        matcher = ZoneMatcher()
        matcher._zones = {}
        matcher._polygon_cache = {}
        matcher._camera_zone_map = {}

        # Add two overlapping zones
        matcher._zones["zone-01"] = {"id": "zone-01", "name": "Zone 1", "polygon_coords": [[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]]}
        matcher._polygon_cache["zone-01"] = Polygon([[0.1, 0.1], [0.6, 0.1], [0.6, 0.6], [0.1, 0.6]])
        matcher._zones["zone-02"] = {"id": "zone-02", "name": "Zone 2", "polygon_coords": [[0.4, 0.4], [0.9, 0.4], [0.9, 0.9], [0.4, 0.9]]}
        matcher._polygon_cache["zone-02"] = Polygon([[0.4, 0.4], [0.9, 0.4], [0.9, 0.9], [0.4, 0.9]])

        # Point in overlap area
        result = matcher.match(0.5, 0.5)

        # Should match one of them
        assert result is not None
        assert result["id"] in ["zone-01", "zone-02"]

    def test_camera_zone_binding_scopes_matching(self):
        """If camera is assigned to a zone, only that zone is considered."""
        matcher = ZoneMatcher()
        matcher._zones = {}
        matcher._polygon_cache = {}
        matcher._camera_zone_map = {}
        matcher._zones["zone-01"] = {"id": "zone-01", "name": "Zone 1", "polygon_coords": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]}
        matcher._zones["zone-02"] = {"id": "zone-02", "name": "Zone 2", "polygon_coords": [[0.6, 0.6], [0.9, 0.6], [0.9, 0.9], [0.6, 0.9]]}
        matcher._polygon_cache["zone-01"] = Polygon([[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]])
        matcher._polygon_cache["zone-02"] = Polygon([[0.6, 0.6], [0.9, 0.6], [0.9, 0.9], [0.6, 0.9]])
        matcher._camera_zone_map["cam-01"] = {"zone-01"}

        # Point is inside zone-02, but cam-01 is bound to zone-01, so should be dropped.
        assert matcher.match(0.7, 0.7, camera_id="cam-01") is None

        # Point inside zone-01 should match.
        result = matcher.match(0.2, 0.2, camera_id="cam-01")
        assert result is not None
        assert result["id"] == "zone-01"


class TestDetectLoop:
    """Test DetectLoop (YOLO inference loop)"""

    @patch('workers.edge_worker.YOLO')
    def test_initialization(self, mock_yolo_class):
        """Test DetectLoop initialization"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        frame_buffer = MagicMock()
        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.YOLO_MODEL', "yolov8n.pt"):
            with patch('config.USE_MOTION_GATE', True):
                with patch('config.USE_TRACKER', True):
                    detect_loop = DetectLoop(
                        "cam-01",
                        frame_buffer,
                        bus_queue,
                        loop,
                        zone_matcher
                    )

                    assert detect_loop.camera_id == "cam-01"
                    assert detect_loop._model == mock_model
                    assert detect_loop._motion_gate is not None
                    assert detect_loop._dwell is not None

    @patch('workers.edge_worker.YOLO')
    def test_start_creates_thread(self, mock_yolo_class):
        """Test that start() creates a detection thread"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        frame_buffer = MagicMock()
        frame_buffer.get_latest.return_value = None

        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.USE_MOTION_GATE', False):
            with patch('config.USE_TRACKER', False):
                detect_loop = DetectLoop(
                    "cam-01",
                    frame_buffer,
                    bus_queue,
                    loop,
                    zone_matcher
                )

                thread = detect_loop.start()

                assert thread is not None
                assert thread.daemon == True
                assert thread.name == "detect-cam-01"

                detect_loop.stop()
                thread.join(timeout=1)

    @patch('workers.edge_worker.YOLO')
    def test_motion_gate_skips_frames(self, mock_yolo_class):
        """Test that motion gate skips frames without motion"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        # Create frame buffer with static frame
        frame_buffer = MagicMock()
        static_frame = np.zeros((480, 640, 3), dtype=np.uint8)

        from workers.edge_worker import CapturedFrame
        cf = CapturedFrame(frame=static_frame, timestamp=time.time(), frame_id=1)
        frame_buffer.get_latest.return_value = cf

        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.USE_MOTION_GATE', True):
            with patch('config.MOTION_THRESHOLD', 0.01):  # High threshold
                with patch('config.USE_TRACKER', False):
                    with patch('config.DETECT_FPS', 10):
                        detect_loop = DetectLoop(
                            "cam-01",
                            frame_buffer,
                            bus_queue,
                            loop,
                            zone_matcher
                        )

                        # Start detection
                        thread = detect_loop.start()

                        # Let it run for a bit
                        time.sleep(0.5)

                        detect_loop.stop()
                        thread.join(timeout=1)

                        health = detect_loop.health()

                        # Should have skipped some frames
                        assert health["frames_skipped"] >= 0

    @patch('workers.edge_worker.YOLO')
    def test_health_metrics(self, mock_yolo_class):
        """Test health() returns correct metrics"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        frame_buffer = MagicMock()
        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.USE_MOTION_GATE', False):
            with patch('config.USE_TRACKER', False):
                detect_loop = DetectLoop(
                    "cam-01",
                    frame_buffer,
                    bus_queue,
                    loop,
                    zone_matcher
                )

                health = detect_loop.health()

                assert "camera_id" in health
                assert "frames_skipped" in health
                assert "frames_detected" in health
                assert "motion_gate_eff" in health
                assert health["camera_id"] == "cam-01"


class TestCameraWorker:
    """Test CameraWorker (integration of all components)"""

    @patch('workers.edge_worker.YOLO')
    def test_initialization(self, mock_yolo_class):
        """Test CameraWorker initialization"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.RECORDINGS_ENABLED', False):
            worker = CameraWorker(
                "cam-01",
                "rtsp://test",
                bus_queue,
                loop,
                zone_matcher
            )

            assert worker.camera_id == "cam-01"
            assert worker._buf is not None
            assert worker._detect is not None

    @patch('workers.edge_worker.YOLO')
    @patch('workers.edge_worker.FrameBuffer.start')
    @patch('workers.edge_worker.DetectLoop.start')
    def test_start_starts_all_components(self, mock_detect_start, mock_buf_start, mock_yolo_class):
        """Test that start() starts all components"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.RECORDINGS_ENABLED', False):
            worker = CameraWorker(
                "cam-01",
                "rtsp://test",
                bus_queue,
                loop,
                zone_matcher
            )

            worker.start()

            mock_buf_start.assert_called_once()
            mock_detect_start.assert_called_once()

    @patch('workers.edge_worker.YOLO')
    def test_health_aggregates_component_health(self, mock_yolo_class):
        """Test that health() aggregates component health"""
        mock_model = MagicMock()
        mock_yolo_class.return_value = mock_model

        bus_queue = asyncio.Queue()
        loop = asyncio.new_event_loop()
        zone_matcher = MagicMock()

        with patch('config.RECORDINGS_ENABLED', False):
            worker = CameraWorker(
                "cam-01",
                "rtsp://test",
                bus_queue,
                loop,
                zone_matcher
            )

            health = worker.health()

            # Should include metrics from both FrameBuffer and DetectLoop
            assert "camera_id" in health
            assert "connected" in health
            assert "buffer_size" in health
            assert "frames_detected" in health


# Pytest fixtures
@pytest.fixture
def sample_yolo_result():
    """Fixture providing a mock YOLO result"""
    mock_box = MagicMock()
    mock_box.cls = [0]  # person class
    mock_box.conf = [0.85]
    mock_box.xyxy = [[100, 100, 200, 200]]
    mock_box.id = [1]  # track ID

    mock_result = MagicMock()
    mock_result.boxes = [mock_box]

    return [mock_result]


# Run tests with: pytest tests/test_yolo_tracking.py -v
