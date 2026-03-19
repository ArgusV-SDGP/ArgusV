"""
workers/recording_worker.py — Continuous FFmpeg Recording
-----------------------------------------------------------
Tasks: REC-01 through REC-09

Runs one FFmpeg subprocess per camera writing HLS .ts segments
to SEGMENT_TMP_DIR. A SegmentWatcher thread detects completed
segments, moves them to LOCAL_RECORDINGS_DIR, inserts a Segment
DB row, and puts a bus event so the detection linker can link
detections to the right segment.

FFmpeg command used:
    ffmpeg -i {rtsp_url} -c copy -f segment -segment_time 10
           -strftime 1 /tmp/argus_segments/{cam}/{cam}_%s.ts
"""

import os
import re
import time
import shutil
import asyncio
import logging
import subprocess
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import config as cfg

logger = logging.getLogger("recording-worker")


class FFmpegRecorder:
    """
    Spawns an FFmpeg process that writes HLS segments continuously.
    Restarts automatically if FFmpeg dies.
    Task REC-01
    """

    def __init__(self, camera_id: str, rtsp_url: str):
        self.camera_id = camera_id
        self.rtsp_url  = rtsp_url
        self._proc: Optional[subprocess.Popen] = None
        self._stop     = threading.Event()
        self._tmp_dir  = Path(cfg.SEGMENT_TMP_DIR) / camera_id
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        t = threading.Thread(target=self._run, daemon=True, name=f"ffmpeg-{self.camera_id}")
        t.start()
        self._thread = t
        return t

    def stop(self):
        self._stop.set()
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._proc = None

    def _run(self):
        while not self._stop.is_set():
            segment_pattern = str(self._tmp_dir / f"{self.camera_id}_%Y%m%d_%H%M%S.ts")
            cmd = [
                "ffmpeg", "-y",
                "-rtsp_transport", "tcp",
                "-i", self.rtsp_url,
                "-map", "0",
                "-c", "copy",
                "-f", "segment",
                "-segment_time", str(cfg.SEGMENT_DURATION_SEC),
                "-segment_format", "mpegts",
                "-strftime", "1",
                "-reset_timestamps", "1",
                "-an", # Drop audio for now to stay simple and high performance
                segment_pattern,
            ]
            logger.info(f"[FFmpeg:{self.camera_id}] Starting: {' '.join(cmd)}")
            try:
                # Don't capture stderr during debug - let it go to docker logs for visibility
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=None,  # FFmpeg output goes to container stderr (visible in docker logs)
                )
                exit_code = self._proc.wait()

                if not self._stop.is_set():
                    logger.warning(f"[FFmpeg:{self.camera_id}] Process exited with code {exit_code} — restarting in 3s")
                    self._stop.wait(3)
            except FileNotFoundError:
                logger.error("[FFmpeg] ffmpeg binary not found! Install ffmpeg.")
                self._stop.wait(30)
            except Exception as e:
                logger.error(f"[FFmpeg:{self.camera_id}] Error: {e}")
                self._stop.wait(5)


class SegmentWatcher:
    """
    Polls SEGMENT_TMP_DIR for completed .ts files.
    A file is "complete" when it hasn't grown in STABLE_SEC seconds.

    For each complete file:
      1. Parses timestamp from filename
      2. Moves to LOCAL_RECORDINGS_DIR
      3. Inserts Segment row in Postgres
      4. Puts segment event on bus

    Task REC-02, REC-03, REC-04, REC-05
    """

    STABLE_SEC   = 5        # file hasn't grown for N seconds → complete
    POLL_SEC     = 2        # how often to check the directory

    def __init__(self, camera_id: str, bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.camera_id  = camera_id
        self._bus_q     = bus_queue
        self._loop      = loop
        self._stop      = threading.Event()
        self._tmp_dir   = Path(cfg.SEGMENT_TMP_DIR) / camera_id
        self._out_dir   = Path(cfg.LOCAL_RECORDINGS_DIR) / camera_id
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._seen: dict[str, tuple[int, float]] = {}   # path → (last_size, stable_since)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        t = threading.Thread(target=self._watch, daemon=True, name=f"watcher-{self.camera_id}")
        t.start()
        self._thread = t
        return t

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _watch(self):
        logger.info(f"[SegmentWatcher:{self.camera_id}] Watching {self._tmp_dir}")
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as e:
                logger.error(f"[SegmentWatcher:{self.camera_id}] Scan error: {e}", exc_info=True)
            self._stop.wait(self.POLL_SEC)

    def _scan(self):
        if not self._tmp_dir.exists():
            logger.warning(f"[SegmentWatcher:{self.camera_id}] Tmp dir doesn't exist: {self._tmp_dir}")
            return

        now = time.time()
        files = list(self._tmp_dir.glob(f"{self.camera_id}_*.ts"))
        if len(files) > 0:
            logger.debug(f"[SegmentWatcher:{self.camera_id}] Scanning {len(files)} files, {len(self._seen)} tracked")

        for f in sorted(files):
            try:
                size = f.stat().st_size
            except FileNotFoundError:
                continue

            if f.name in self._seen:
                last_size, stable_since = self._seen[f.name]
                if size == last_size:
                    stable_duration = now - stable_since
                    # File hasn't grown. Check IF it's been stable long enough.
                    if stable_duration >= self.STABLE_SEC:
                        logger.info(f"[SegmentWatcher:{self.camera_id}] File stable for {stable_duration:.1f}s: {f.name} ({size} bytes)")
                        if self._process_segment(f):
                            del self._seen[f.name]
                        else:
                            logger.warning(f"[SegmentWatcher:{self.camera_id}] Process failed, will retry: {f.name}")
                    else:
                        logger.debug(f"[SegmentWatcher:{self.camera_id}] Waiting for stability: {f.name} ({stable_duration:.1f}/{self.STABLE_SEC}s)")
                else:
                    # File grew, reset stability timer
                    logger.debug(f"[SegmentWatcher:{self.camera_id}] File growing: {f.name} ({last_size} → {size} bytes)")
                    self._seen[f.name] = (size, now)
            else:
                # First time seeing this file
                logger.debug(f"[SegmentWatcher:{self.camera_id}] New file detected: {f.name} ({size} bytes)")
                self._seen[f.name] = (size, now)

    def _process_segment(self, ts_file: Path) -> bool:
        """
        Returns True if successfully processed, False to retry later (e.g. locked).
        """
        try:
            # 1. Parse timestamp
            m = re.search(r"_(\d{8}_\d{6})\.ts$", ts_file.name)
            if not m:
                logger.warning(f"[SegmentWatcher] Invalid filename: {ts_file.name}")
                return True # Don't retry invalid files

            ts_str = m.group(1)
            # Parse local-time filename timestamp, convert to UTC for DB consistency
            # (Detections are stored in UTC, so segment times must match)
            from datetime import timezone as _tz
            start_dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").astimezone(_tz.utc).replace(tzinfo=None)
            end_dt   = start_dt + timedelta(seconds=cfg.SEGMENT_DURATION_SEC)
            size     = ts_file.stat().st_size

            # 2. Try to move (Windows check: WinError 32 happens here)
            dest = self._out_dir / ts_file.name
            if dest.exists():
                logger.warning(
                    f"[SegmentWatcher:{self.camera_id}] Destination already exists, skipping move: {dest.name}"
                )
                return True
            shutil.move(str(ts_file), str(dest))
            
            # 3. DB write
            self._write_db(str(dest), start_dt, end_dt, size)
            
            # 4. Bus event
            seg_event = {
                "event_type": "SEGMENT_COMPLETE",
                "camera_id":  self.camera_id,
                "path":       str(dest),
                "start_time": start_dt.isoformat(),
                "end_time":   end_dt.isoformat(),
                "size_bytes": size,
            }
            asyncio.run_coroutine_threadsafe(self._bus_q.put(seg_event), self._loop)
            logger.info(f"[SegmentWatcher:{self.camera_id}] Segment moved/stored: {dest.name}")
            return True

        except (PermissionError, OSError) as e:
            # WinError 32 is a PermissionError on Windows
            if getattr(e, 'winerror', None) == 32 or isinstance(e, PermissionError):
                logger.debug(f"[SegmentWatcher:{self.camera_id}] File locked (waiting): {ts_file.name}")
                return False
            logger.error(f"[SegmentWatcher] Error handling {ts_file.name}: {e}")
            return False
        except Exception as e:
            logger.error(f"[SegmentWatcher] Unexpected error processing {ts_file.name}: {e}")
            return False

    def _write_db(self, local_path: str, start_dt: datetime, end_dt: datetime, size: int):
        from db.connection import get_db_sync
        from db.models import Segment
        db = get_db_sync()
        try:
            seg = Segment(
                camera_id    = self.camera_id,
                start_time   = start_dt,
                end_time     = end_dt,
                duration_sec = cfg.SEGMENT_DURATION_SEC,
                minio_path   = local_path,   # reuse minio_path for local path
                size_bytes   = size,
            )
            db.add(seg)
            db.commit()
            logger.debug(f"[SegmentWatcher] Inserted Segment DB row for — {local_path}")
        except Exception as e:
            logger.error(f"[SegmentWatcher] Failed to write DB segment: {e}")
            db.rollback()
        finally:
            db.close()


class CameraRecorder:
    """
    Ties FFmpegRecorder + SegmentWatcher together for one camera.
    Called by edge_worker.py when RECORDINGS_ENABLED=true.
    """

    def __init__(self, camera_id: str, rtsp_url: str,
                 bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        logger.info(f"[CameraRecorder:{camera_id}] Initializing with RTSP: {rtsp_url}")
        self._recorder = FFmpegRecorder(camera_id, rtsp_url)
        self._watcher  = SegmentWatcher(camera_id, bus_queue, loop)
        logger.info(f"[CameraRecorder:{camera_id}] ✅ Created")

    def start(self):
        logger.info(f"[CameraRecorder:{self._recorder.camera_id}] Starting FFmpegRecorder and SegmentWatcher")
        self._recorder.start()
        self._watcher.start()
        logger.info(f"[CameraRecorder:{self._recorder.camera_id}] ✅ Started")

    def stop(self):
        self._recorder.stop()
        self._watcher.stop()
