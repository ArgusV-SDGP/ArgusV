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

from ..config import *

logger = logging.getLogger("recording-worker")

LOCAL_RECORDINGS_DIR = os.getenv("LOCAL_RECORDINGS_DIR", "/recordings")


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
        self._tmp_dir  = Path(SEGMENT_TMP_DIR) / camera_id
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._run, daemon=True, name=f"ffmpeg-{self.camera_id}")
        t.start()
        return t

    def stop(self):
        self._stop.set()
        if self._proc:
            self._proc.terminate()

    def _run(self):
        while not self._stop.is_set():
            segment_pattern = str(self._tmp_dir / f"{self.camera_id}_%s.ts")
            cmd = [
                "ffmpeg", "-y",
                "-rtsp_transport", "tcp",
                "-i", self.rtsp_url,
                "-c", "copy",
                "-f", "segment",
                "-segment_time", str(SEGMENT_DURATION_SEC),
                "-segment_format", "mpegts",
                "-strftime", "1",
                "-reset_timestamps", "1",
                segment_pattern,
            ]
            logger.info(f"[FFmpeg:{self.camera_id}] Starting: {' '.join(cmd)}")
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._proc.wait()
                if not self._stop.is_set():
                    logger.warning(f"[FFmpeg:{self.camera_id}] Process exited — restarting in 3s")
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

    STABLE_SEC   = 3        # file hasn't grown for N seconds → complete
    POLL_SEC     = 2        # how often to check the directory

    def __init__(self, camera_id: str, bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self.camera_id  = camera_id
        self._bus_q     = bus_queue
        self._loop      = loop
        self._stop      = threading.Event()
        self._tmp_dir   = Path(SEGMENT_TMP_DIR) / camera_id
        self._out_dir   = Path(LOCAL_RECORDINGS_DIR) / camera_id
        self._out_dir.mkdir(parents=True, exist_ok=True)
        self._seen: dict[str, float] = {}   # path → last_size

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._watch, daemon=True, name=f"watcher-{self.camera_id}")
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _watch(self):
        logger.info(f"[SegmentWatcher:{self.camera_id}] Watching {self._tmp_dir}")
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as e:
                logger.error(f"[SegmentWatcher:{self.camera_id}] Scan error: {e}")
            self._stop.wait(self.POLL_SEC)

    def _scan(self):
        if not self._tmp_dir.exists():
            return
        for f in sorted(self._tmp_dir.glob(f"{self.camera_id}_*.ts")):
            size = f.stat().st_size
            if f.name in self._seen:
                if size == self._seen[f.name]:
                    # File hasn't grown → complete
                    self._process_segment(f)
                    del self._seen[f.name]
                    continue
            self._seen[f.name] = size

    def _process_segment(self, ts_file: Path):
        # TODO REC-03: Parse epoch timestamp from filename
        # cam-01_1709150400.ts → start_time = datetime(2024,2,28,17,0,0)
        m = re.search(r"_(\d+)\.ts$", ts_file.name)
        if not m:
            logger.warning(f"[SegmentWatcher] Cannot parse timestamp from {ts_file.name}")
            return

        epoch_start = int(m.group(1))
        start_dt    = datetime.utcfromtimestamp(epoch_start)
        end_dt      = start_dt + timedelta(seconds=SEGMENT_DURATION_SEC)
        size        = ts_file.stat().st_size

        # TODO REC-03: Move to recordings dir
        dest = self._out_dir / ts_file.name
        shutil.move(str(ts_file), str(dest))

        local_path = str(dest)

        # TODO REC-04: Insert Segment row to DB
        self._write_db(local_path, start_dt, end_dt, size)

        # TODO REC-05: Put on bus
        seg_event = {
            "type":       "segment_registered",
            "camera_id":  self.camera_id,
            "local_path": local_path,
            "start_time": start_dt.isoformat(),
            "end_time":   end_dt.isoformat(),
            "size_bytes": size,
        }
        asyncio.run_coroutine_threadsafe(self._bus_q.put(seg_event), self._loop)
        logger.info(f"[SegmentWatcher:{self.camera_id}] Segment complete: {dest.name} ({size//1024}KB)")

    def _write_db(self, local_path: str, start_dt: datetime, end_dt: datetime, size: int):
        # TODO REC-04: implement DB write
        # from db.connection import get_db_sync
        # from db.models import Segment
        # import uuid
        # db = get_db_sync()
        # try:
        #     seg = Segment(
        #         camera_id    = self.camera_id,
        #         start_time   = start_dt,
        #         end_time     = end_dt,
        #         duration_sec = cfg.SEGMENT_DURATION_SEC,
        #         minio_path   = local_path,   # reuse minio_path for local path
        #         size_bytes   = size,
        #     )
        #     db.add(seg)
        #     db.commit()
        # finally:
        #     db.close()
        logger.debug(f"[SegmentWatcher] TODO REC-04: write segment to DB — {local_path}")


class CameraRecorder:
    """
    Ties FFmpegRecorder + SegmentWatcher together for one camera.
    Called by edge_worker.py when RECORDINGS_ENABLED=true.
    """

    def __init__(self, camera_id: str, rtsp_url: str,
                 bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
        self._recorder = FFmpegRecorder(camera_id, rtsp_url)
        self._watcher  = SegmentWatcher(camera_id, bus_queue, loop)

    def start(self):
        self._recorder.start()
        self._watcher.start()

    def stop(self):
        self._recorder.stop()
        self._watcher.stop()
