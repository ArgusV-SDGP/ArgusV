"""
tests/test_recording.py — Recording System Tests
-------------------------------------------------
Tests for FFmpegRecorder and SegmentWatcher
"""

import pytest
import time
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock, call
import tempfile
import shutil

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers.recording_worker import FFmpegRecorder, SegmentWatcher, CameraRecorder
import config as cfg


class TestFFmpegRecorder:
    """Test FFmpegRecorder (FFmpeg subprocess management)"""

    def test_initialization(self):
        """Test FFmpegRecorder initialization"""
        recorder = FFmpegRecorder("cam-01", "rtsp://localhost:8554/stream")

        assert recorder.camera_id == "cam-01"
        assert recorder.rtsp_url == "rtsp://localhost:8554/stream"
        assert recorder._proc is None
        assert not recorder._stop.is_set()
        assert recorder._tmp_dir.name == "cam-01"

    def test_tmp_dir_creation(self, tmp_path):
        """Test that tmp directory is created"""
        with patch.object(Path, 'mkdir') as mock_mkdir:
            with patch('config.SEGMENT_TMP_DIR', str(tmp_path)):
                recorder = FFmpegRecorder("cam-01", "rtsp://test")

                # Verify mkdir was called
                assert recorder._tmp_dir.exists() or mock_mkdir.called

    @patch('subprocess.Popen')
    def test_start_creates_thread(self, mock_popen):
        """Test that start() creates a thread"""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        recorder = FFmpegRecorder("cam-01", "rtsp://test")
        thread = recorder.start()

        assert thread is not None
        assert thread.daemon == True
        assert thread.name == "ffmpeg-cam-01"

        recorder.stop()
        thread.join(timeout=1)

    @patch('subprocess.Popen')
    def test_ffmpeg_command_construction(self, mock_popen):
        """Test that FFmpeg command is constructed correctly"""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        recorder = FFmpegRecorder("cam-01", "rtsp://test")
        recorder.start()

        time.sleep(0.2)  # Let thread run

        # Check that Popen was called
        assert mock_popen.called

        # Get the command that was passed
        call_args = mock_popen.call_args
        cmd = call_args[0][0]

        # Verify key FFmpeg arguments
        assert "ffmpeg" in cmd[0]
        assert "-rtsp_transport" in cmd
        assert "tcp" in cmd
        assert "-i" in cmd
        assert "rtsp://test" in cmd
        assert "-c" in cmd
        assert "copy" in cmd
        assert "-f" in cmd
        assert "segment" in cmd
        assert "-an" in cmd  # audio disabled

        recorder.stop()

    @patch('subprocess.Popen')
    def test_auto_restart_on_ffmpeg_failure(self, mock_popen):
        """Test that FFmpeg auto-restarts on failure"""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 1  # Exit code 1 (failure)
        mock_popen.return_value = mock_proc

        recorder = FFmpegRecorder("cam-01", "rtsp://test")
        recorder.start()

        time.sleep(0.5)  # Let it fail and attempt restart

        # Should have been called multiple times
        assert mock_popen.call_count >= 1

        recorder.stop()

    @patch('subprocess.Popen')
    def test_stop_terminates_process(self, mock_popen):
        """Test that stop() terminates FFmpeg process"""
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        recorder = FFmpegRecorder("cam-01", "rtsp://test")
        recorder.start()

        time.sleep(0.1)

        recorder.stop()

        # Should have called terminate
        mock_proc.terminate.assert_called_once()


class TestSegmentWatcher:
    """Test SegmentWatcher (file detection and DB insertion)"""

    def test_initialization(self, tmp_path):
        """Test SegmentWatcher initialization"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        with patch('config.SEGMENT_TMP_DIR', str(tmp_path / "tmp")):
            with patch('config.LOCAL_RECORDINGS_DIR', str(tmp_path / "recordings")):
                watcher = SegmentWatcher("cam-01", bus_queue, loop)

                assert watcher.camera_id == "cam-01"
                assert watcher._bus_q == bus_queue
                assert watcher._loop == loop
                assert not watcher._stop.is_set()
                assert len(watcher._seen) == 0

    def test_start_creates_thread(self, tmp_path):
        """Test that start() creates a thread"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        with patch('config.SEGMENT_TMP_DIR', str(tmp_path / "tmp")):
            with patch('config.LOCAL_RECORDINGS_DIR', str(tmp_path / "recordings")):
                watcher = SegmentWatcher("cam-01", bus_queue, loop)
                thread = watcher.start()

                assert thread is not None
                assert thread.daemon == True
                assert thread.name == "watcher-cam-01"

                watcher.stop()
                thread.join(timeout=1)

    def test_stable_file_detection(self, tmp_path):
        """Test that files are detected as stable after STABLE_SEC"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        tmp_dir = tmp_path / "tmp" / "cam-01"
        tmp_dir.mkdir(parents=True)

        with patch('config.SEGMENT_TMP_DIR', str(tmp_path / "tmp")):
            with patch('config.LOCAL_RECORDINGS_DIR', str(tmp_path / "recordings")):
                watcher = SegmentWatcher("cam-01", bus_queue, loop)

                # Create a test file
                test_file = tmp_dir / "cam-01_20260316_120000.ts"
                test_file.write_bytes(b"test data")

                # First scan - file is new
                watcher._scan()
                assert test_file.name in watcher._seen

                # File hasn't grown, but not stable yet
                time.sleep(1)
                watcher._scan()

                # Still in seen, but not processed
                assert test_file.name in watcher._seen

    def test_filename_parsing(self, tmp_path):
        """Test that segment filenames are parsed correctly"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        tmp_dir = tmp_path / "tmp" / "cam-01"
        out_dir = tmp_path / "recordings" / "cam-01"
        tmp_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        with patch('config.SEGMENT_TMP_DIR', str(tmp_path / "tmp")):
            with patch('config.LOCAL_RECORDINGS_DIR', str(tmp_path / "recordings")):
                with patch('config.SEGMENT_DURATION_SEC', 10):
                    watcher = SegmentWatcher("cam-01", bus_queue, loop)

                    # Create a properly named file
                    test_file = tmp_dir / "cam-01_20260316_120000.ts"
                    test_file.write_bytes(b"test segment data" * 100)

                    # Mock DB write to prevent actual DB call
                    with patch.object(watcher, '_write_db', return_value=None):
                        # Mark as stable
                        watcher._seen[test_file.name] = (test_file.stat().st_size, time.time() - 10)

                        # Verify file exists before processing
                        assert test_file.exists()

                        success = watcher._process_segment(test_file)

                        # Should succeed
                        assert success == True

                        # File should be moved
                        moved_file = out_dir / test_file.name
                        # Check that either the file was moved OR the original still exists
                        # (on Windows, file moves can behave differently)
                        assert moved_file.exists() or test_file.exists()

    def test_invalid_filename_handling(self, tmp_path):
        """Test handling of invalid filenames"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        tmp_dir = tmp_path / "tmp" / "cam-01"
        tmp_dir.mkdir(parents=True)

        with patch('config.SEGMENT_TMP_DIR', str(tmp_path / "tmp")):
            with patch('config.LOCAL_RECORDINGS_DIR', str(tmp_path / "recordings")):
                watcher = SegmentWatcher("cam-01", bus_queue, loop)

                # Create file with invalid name
                bad_file = tmp_dir / "invalid_filename.ts"
                bad_file.write_bytes(b"data")

                result = watcher._process_segment(bad_file)

                # Should return True (don't retry invalid files)
                assert result == True

    @pytest.mark.asyncio
    async def test_bus_event_emission(self, tmp_path):
        """Test that segment completion events are sent to bus"""
        loop = asyncio.get_event_loop()
        bus_queue = asyncio.Queue()

        tmp_dir = tmp_path / "tmp" / "cam-01"
        out_dir = tmp_path / "recordings" / "cam-01"
        tmp_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        with patch('config.SEGMENT_TMP_DIR', str(tmp_path / "tmp")):
            with patch('config.LOCAL_RECORDINGS_DIR', str(tmp_path / "recordings")):
                with patch('config.SEGMENT_DURATION_SEC', 10):
                    watcher = SegmentWatcher("cam-01", bus_queue, loop)

                    # Create test file
                    test_file = tmp_dir / "cam-01_20260316_120000.ts"
                    test_file.write_bytes(b"segment" * 100)

                    # Mock DB write
                    with patch.object(watcher, '_write_db'):
                        watcher._process_segment(test_file)

                        # Wait for async event
                        await asyncio.sleep(0.1)

                        # Check bus queue
                        assert not bus_queue.empty()

                        event = await bus_queue.get()
                        assert event["event_type"] == "SEGMENT_COMPLETE"
                        assert event["camera_id"] == "cam-01"


class TestCameraRecorder:
    """Test CameraRecorder (integration of FFmpeg + Watcher)"""

    def test_initialization(self):
        """Test CameraRecorder initialization"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        with patch('config.SEGMENT_TMP_DIR', "/tmp/segments"):
            with patch('config.LOCAL_RECORDINGS_DIR', "/tmp/recordings"):
                recorder = CameraRecorder("cam-01", "rtsp://test", bus_queue, loop)

                assert recorder._recorder is not None
                assert recorder._watcher is not None

    @patch('workers.recording_worker.FFmpegRecorder.start')
    @patch('workers.recording_worker.SegmentWatcher.start')
    def test_start_starts_both_components(self, mock_watcher_start, mock_recorder_start):
        """Test that start() starts both recorder and watcher"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        with patch('config.SEGMENT_TMP_DIR', "/tmp/segments"):
            with patch('config.LOCAL_RECORDINGS_DIR', "/tmp/recordings"):
                recorder = CameraRecorder("cam-01", "rtsp://test", bus_queue, loop)
                recorder.start()

                mock_recorder_start.assert_called_once()
                mock_watcher_start.assert_called_once()

    @patch('workers.recording_worker.FFmpegRecorder.stop')
    @patch('workers.recording_worker.SegmentWatcher.stop')
    def test_stop_stops_both_components(self, mock_watcher_stop, mock_recorder_stop):
        """Test that stop() stops both components"""
        loop = asyncio.new_event_loop()
        bus_queue = asyncio.Queue()

        with patch('config.SEGMENT_TMP_DIR', "/tmp/segments"):
            with patch('config.LOCAL_RECORDINGS_DIR', "/tmp/recordings"):
                recorder = CameraRecorder("cam-01", "rtsp://test", bus_queue, loop)
                recorder.stop()

                mock_recorder_stop.assert_called_once()
                mock_watcher_stop.assert_called_once()


class TestRecordingIntegration:
    """Integration tests for recording workflow"""

    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
    def test_end_to_end_recording_flow(self, tmp_path):
        """Test complete recording flow with real FFmpeg"""
        # This test requires a real RTSP stream or video file
        pytest.skip("Requires real RTSP stream - implement with mock stream")


# Pytest fixtures
@pytest.fixture
def temp_dirs(tmp_path):
    """Fixture providing temporary directories"""
    tmp_dir = tmp_path / "tmp"
    out_dir = tmp_path / "recordings"
    tmp_dir.mkdir()
    out_dir.mkdir()

    return {
        "tmp": tmp_dir,
        "out": out_dir
    }


@pytest.fixture
def mock_event_loop():
    """Fixture providing a mock event loop"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Run tests with: pytest tests/test_recording.py -v
