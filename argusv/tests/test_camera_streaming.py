"""
tests/test_camera_streaming.py — Camera Streaming Tests
--------------------------------------------------------
Tests for FrameBuffer (RTSP capture and buffering)
"""

import pytest
import time
import threading
import numpy as np
import cv2
from unittest.mock import Mock, patch, MagicMock
from collections import deque

# Import components from edge_worker
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers.edge_worker import FrameBuffer, CapturedFrame


class TestCapturedFrame:
    """Test the CapturedFrame dataclass"""

    def test_captured_frame_creation(self):
        """Test creating a CapturedFrame instance"""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        timestamp = time.time()
        frame_id = 42

        cf = CapturedFrame(frame=frame, timestamp=timestamp, frame_id=frame_id)

        assert cf.frame.shape == (480, 640, 3)
        assert cf.timestamp == timestamp
        assert cf.frame_id == frame_id


class TestFrameBuffer:
    """Test the FrameBuffer class (RTSP capture)"""

    def test_initialization(self):
        """Test FrameBuffer initialization"""
        rtsp_url = "rtsp://localhost:8554/test"
        camera_id = "cam-test"

        fb = FrameBuffer(rtsp_url, camera_id, max_seconds=10, fps=5)

        assert fb.rtsp_url == rtsp_url
        assert fb.camera_id == camera_id
        assert fb._max_frames == 50  # 10s * 5fps
        assert fb._target_fps == 5
        assert len(fb._buffer) == 0
        assert fb._connected == False
        assert fb._frame_count == 0
        assert fb._bad_frame_count == 0
        assert fb._max_bad_frames == 10
        assert fb._last_good_frame_ts == 0.0

    def test_health_initial_state(self):
        """Test health() returns correct initial state"""
        fb = FrameBuffer("rtsp://test", "cam-01")
        health = fb.health()

        assert health["camera_id"] == "cam-01"
        assert health["connected"] == False
        assert health["buffer_size"] == 0
        assert health["frame_count"] == 0

    def test_get_latest_empty_buffer(self):
        """Test get_latest() with empty buffer"""
        fb = FrameBuffer("rtsp://test", "cam-01")

        assert fb.get_latest() is None

    def test_get_frames_empty_buffer(self):
        """Test get_frames() with empty buffer"""
        fb = FrameBuffer("rtsp://test", "cam-01")

        frames = fb.get_frames(n=5)
        assert frames == []

    def test_buffer_thread_safety(self):
        """Test that buffer operations are thread-safe"""
        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=2, fps=10)

        # Simulate adding frames from different threads
        def add_frame(frame_id):
            with fb._lock:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cf = CapturedFrame(frame=frame, timestamp=time.time(), frame_id=frame_id)
                fb._buffer.append(cf)

        threads = []
        for i in range(10):
            t = threading.Thread(target=add_frame, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(fb._buffer) == 10

    def test_buffer_maxlen_enforcement(self):
        """Test that buffer respects maxlen"""
        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=1, fps=5)  # max 5 frames

        # Add 10 frames
        for i in range(10):
            with fb._lock:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cf = CapturedFrame(frame=frame, timestamp=time.time(), frame_id=i)
                fb._buffer.append(cf)

        # Should only keep the latest 5
        assert len(fb._buffer) == 5
        assert fb._buffer[0].frame_id == 5
        assert fb._buffer[-1].frame_id == 9

    @patch('cv2.VideoCapture')
    def test_start_creates_thread(self, mock_vc):
        """Test that start() creates a daemon thread"""
        fb = FrameBuffer("rtsp://test", "cam-01")

        fb.start()

        assert fb._thread is not None
        assert fb._thread.is_alive()
        assert fb._thread.daemon == True
        assert fb._thread.name == "framebuf-cam-01"

        # Cleanup
        fb.stop()

    @patch('cv2.VideoCapture')
    def test_stop_sets_event_and_joins_thread(self, mock_vc):
        """Test that stop() properly cleans up"""
        fb = FrameBuffer("rtsp://test", "cam-01")
        fb.start()

        time.sleep(0.1)  # Let thread start

        fb.stop()

        # Wait a bit longer for thread to fully stop
        time.sleep(0.5)

        assert fb._stop.is_set()
        # Thread should be dead or None (daemon threads may take time to stop)
        if fb._thread:
            assert not fb._thread.is_alive()
        assert fb._connected == False

    @patch('cv2.VideoCapture')
    def test_capture_loop_reconnect_on_failure(self, mock_vc_class):
        """Test that capture loop reconnects on stream failure"""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False  # Simulate connection failure
        mock_vc_class.return_value = mock_cap

        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=1, fps=1)
        fb.start()

        time.sleep(0.5)  # Let it attempt connection

        # Should have attempted to create VideoCapture
        assert mock_vc_class.called

        fb.stop()

    @patch('cv2.VideoCapture')
    def test_capture_loop_with_successful_frames(self, mock_vc_class):
        """Test successful frame capture"""
        # Create mock VideoCapture
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True

        # Create test frame
        test_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        mock_cap.read.return_value = (True, test_frame)
        mock_vc_class.return_value = mock_cap

        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=1, fps=2)
        fb.start()

        time.sleep(1.0)  # Let it capture some frames

        fb.stop()

        # Should have captured at least 1 frame
        assert fb._frame_count > 0
        assert len(fb._buffer) > 0

        latest = fb.get_latest()
        assert latest is not None
        assert latest.frame.shape == (480, 640, 3)

    def test_get_frames_returns_latest_n(self):
        """Test get_frames() returns last N frames"""
        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=2, fps=10)

        # Add 10 frames
        for i in range(10):
            with fb._lock:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cf = CapturedFrame(frame=frame, timestamp=time.time(), frame_id=i)
                fb._buffer.append(cf)

        frames = fb.get_frames(n=5)

        assert len(frames) == 5
        assert frames[0].frame_id == 5
        assert frames[-1].frame_id == 9

    @patch('cv2.VideoCapture')
    def test_bad_frame_handling(self, mock_vc_class):
        """Test that bad frames trigger reconnection"""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True

        # Return bad frames (None)
        read_results = [(False, None)] * 15 + [(True, np.zeros((480, 640, 3), dtype=np.uint8))]
        mock_cap.read.side_effect = read_results
        mock_vc_class.return_value = mock_cap

        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=1, fps=5)
        fb.start()

        time.sleep(2.0)  # Let it process bad frames

        fb.stop()

        # Should have reset bad_frame_count after exceeding max
        # This tests the reconnection logic
        assert mock_cap.release.called

    def test_frame_rate_limiting(self):
        """Test that frames are captured at target FPS"""
        fb = FrameBuffer("rtsp://test", "cam-01", max_seconds=1, fps=5)

        # Calculate expected interval
        expected_interval = 1.0 / 5  # 0.2 seconds

        assert fb._target_fps == 5

        # The interval calculation in _capture_loop should be 0.2s
        calculated_interval = 1.0 / fb._target_fps
        assert calculated_interval == pytest.approx(expected_interval, rel=1e-6)


class TestFrameBufferIntegration:
    """Integration tests for FrameBuffer with real OpenCV"""

    @pytest.mark.skipif(not hasattr(cv2, 'VideoCapture'), reason="OpenCV not available")
    def test_framebuffer_with_test_video(self, tmp_path):
        """Test FrameBuffer with a synthetic test video"""
        # Create a simple test video file
        video_path = tmp_path / "test_video.mp4"

        # Generate test video using OpenCV
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(str(video_path), fourcc, 5.0, (640, 480))

        # Write 25 frames (5 seconds @ 5fps)
        for i in range(25):
            frame = np.full((480, 640, 3), i * 10, dtype=np.uint8)
            out.write(frame)

        out.release()

        # Test FrameBuffer with the video file
        fb = FrameBuffer(str(video_path), "cam-test", max_seconds=2, fps=5)
        fb.start()

        time.sleep(2.0)  # Let it capture frames

        fb.stop()

        assert fb._frame_count > 0
        assert len(fb._buffer) > 0


# Pytest fixtures
@pytest.fixture
def sample_frame():
    """Fixture providing a sample frame"""
    return np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)


@pytest.fixture
def frame_buffer():
    """Fixture providing a FrameBuffer instance"""
    fb = FrameBuffer("rtsp://test", "cam-test", max_seconds=5, fps=5)
    yield fb
    if fb._thread and fb._thread.is_alive():
        fb.stop()


# Run tests with: pytest tests/test_camera_streaming.py -v
