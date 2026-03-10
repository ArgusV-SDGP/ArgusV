"""
workers/edge_worker.py — Camera + Detection Worker (self-contained)
---------------------------------------------------------------------
FrameBuffer, MotionGate, DwellTracker all defined inline.
No imports from sibling projects needed.

Per camera runs in threads:
  1. FrameBuffer   — RTSP decode → rolling deque
  2. DetectLoop    — MotionGate → YOLO → ZoneMatcher → DwellTracker
  3. CameraRecorder — optional FFmpeg recording
"""

import sys
import time
import uuid
import json
import base64
import logging
import asyncio
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable
import cv2
import numpy as np

import config as cfg

logger = logging.getLogger("edge-worker")


# ══════════════════════════════════════════════════════════════════════════════
# FrameBuffer — RTSP → rolling frame deque
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CapturedFrame:
    frame: np.ndarray
    timestamp: float
    frame_id: int


class FrameBuffer:
    """
    Reads RTSP stream in a daemon thread.
    Stores the latest N frames in a deque with auto-reconnect.
    """

    def __init__(self, rtsp_url: str, camera_id: str,
                 max_seconds: int = 10, fps: int = 5):
        self.rtsp_url     = rtsp_url
        self.camera_id    = camera_id
        self._max_frames  = max_seconds * fps
        self._target_fps  = fps
        self._buffer: deque[CapturedFrame] = deque(maxlen=self._max_frames)
        self._lock        = threading.Lock()
        self._stop        = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame_count = 0
        self._connected   = False
        self._cap: Optional[cv2.VideoCapture] = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True,
            name=f"framebuf-{self.camera_id}",
        )
        self._thread.start()
        logger.info(f"[FrameBuffer:{self.camera_id}] Started → {self.rtsp_url}")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._connected = False

    def get_latest(self) -> Optional[CapturedFrame]:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_frames(self, n: int = 5) -> list[CapturedFrame]:
        with self._lock:
            return list(self._buffer)[-n:]

    def health(self) -> dict:
        return {
            "camera_id":   self.camera_id,
            "connected":   self._connected,
            "buffer_size": len(self._buffer),
            "frame_count": self._frame_count,
        }

    def _capture_loop(self):
        interval     = 1.0 / self._target_fps
        retry_delay  = 1.0

        while not self._stop.is_set():
            if not self._connected:
                try:
                    self._cap = cv2.VideoCapture(self.rtsp_url)
                    if not self._cap.isOpened():
                        raise ConnectionError("Failed to open stream")
                    self._connected = True
                    retry_delay = 1.0
                    logger.info(f"[FrameBuffer:{self.camera_id}] Connected")
                except Exception as e:
                    logger.warning(
                        f"[FrameBuffer:{self.camera_id}] Connect error: {e}, "
                        f"retry in {retry_delay:.0f}s"
                    )
                    self._stop.wait(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
                    continue

            t0 = time.monotonic()
            ret, frame = self._cap.read()
            if not ret or frame is None:
                logger.warning(f"[FrameBuffer:{self.camera_id}] Read failed — reconnecting")
                self._connected = False
                self._cap.release()
                continue

            cf = CapturedFrame(
                frame=frame,
                timestamp=time.time(),
                frame_id=self._frame_count,
            )
            with self._lock:
                self._buffer.append(cf)
            self._frame_count += 1

            elapsed = time.monotonic() - t0
            self._stop.wait(max(0, interval - elapsed))


# ══════════════════════════════════════════════════════════════════════════════
# MotionGate — background subtraction pre-filter
# ══════════════════════════════════════════════════════════════════════════════

class MotionGate:
    """
    Uses OpenCV MOG2 background subtraction.
    Returns True if motion exceeds threshold (run YOLO),
    False to skip (no motion).
    """

    def __init__(self, threshold: float = 0.003):
        self._threshold  = threshold
        self._bg_sub     = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16, detectShadows=False,
        )
        self._warmup_frames = 30
        self._frame_count   = 0

    def has_motion(self, frame: np.ndarray) -> bool:
        fg = self._bg_sub.apply(frame)
        self._frame_count += 1
        if self._frame_count < self._warmup_frames:
            return True
        return np.count_nonzero(fg) / fg.size > self._threshold


# ══════════════════════════════════════════════════════════════════════════════
# DwellTracker — loitering / event lifecycle tracker
# ══════════════════════════════════════════════════════════════════════════════

class DwellTracker:
    """
    Tracks per-object dwell time in zones.
    Emits: START, UPDATE, LOITERING, END events.
    """

    def __init__(self,
                 on_event: Callable,
                 camera_id: str,
                 loiter_threshold_sec: float = 30,
                 update_interval_sec: float  = 10,
                 evict_after_sec: float      = 5):
        self._on_event   = on_event
        self._camera_id  = camera_id
        self._loiter_sec = loiter_threshold_sec
        self._update_sec = update_interval_sec
        self._evict_sec  = evict_after_sec
        self._tracks: dict[int, dict] = {}
        self._lock  = threading.Lock()
        self._stop  = threading.Event()

        threading.Thread(
            target=self._eviction_loop, daemon=True,
            name=f"dwell-{camera_id}",
        ).start()

    def update(self, track_id: int, zone_id: str, zone_name: str, event: dict):
        now = time.time()
        with self._lock:
            if track_id not in self._tracks:
                self._tracks[track_id] = {
                    "first_seen":         now,
                    "last_seen":          now,
                    "last_update_emit":   now,
                    "loitering_emitted":  False,
                    "base_event":         event,
                }
                self._on_event({**event, "event_type": "START", "dwell_sec": 0})
                return

            t = self._tracks[track_id]
            t["last_seen"] = now
            dwell = now - t["first_seen"]

            if dwell >= self._loiter_sec and not t["loitering_emitted"]:
                t["loitering_emitted"] = True
                self._on_event({**event, "event_type": "LOITERING", "dwell_sec": round(dwell, 1)})
            elif now - t["last_update_emit"] >= self._update_sec:
                t["last_update_emit"] = now
                self._on_event({**event, "event_type": "UPDATE",    "dwell_sec": round(dwell, 1)})

    def flush_all(self):
        with self._lock:
            for t in self._tracks.values():
                dwell = time.time() - t["first_seen"]
                self._on_event({**t["base_event"], "event_type": "END", "dwell_sec": round(dwell, 1)})
            self._tracks.clear()
        self._stop.set()

    def _eviction_loop(self):
        while not self._stop.is_set():
            self._stop.wait(2.0)
            now = time.time()
            with self._lock:
                stale = [tid for tid, t in self._tracks.items()
                         if now - t["last_seen"] > self._evict_sec]
                for tid in stale:
                    t = self._tracks.pop(tid)
                    dwell = t["last_seen"] - t["first_seen"]
                    self._on_event({**t["base_event"], "event_type": "END", "dwell_sec": round(dwell, 1)})


# ══════════════════════════════════════════════════════════════════════════════
# ZoneMatcher — loads zones from Postgres, hot-reloads via Redis
# ══════════════════════════════════════════════════════════════════════════════

class ZoneMatcher:
    def __init__(self):
        self._zones: dict = {}
        self._lock = threading.Lock()
        self._load_from_db()
        self._start_redis_listener()

    def _load_from_db(self):
        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(cfg.POSTGRES_URL)
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT zone_id, name, polygon_coords, active FROM zones")
                ).fetchall()
            with self._lock:
                self._zones = {
                    str(r.zone_id): {
                        "id": str(r.zone_id),
                        "name": r.name,
                        "polygon_coords": (
                            json.loads(r.polygon_coords)
                            if isinstance(r.polygon_coords, str)
                            else r.polygon_coords
                        ),
                    }
                    for r in rows if r.active
                }
            logger.info(f"[ZoneMatcher] Loaded {len(self._zones)} zones from DB")
        except Exception as e:
            logger.warning(f"[ZoneMatcher] DB load failed (no zones mode): {e}")

    def _start_redis_listener(self):
        def _listen():
            try:
                import redis as _redis
                r  = _redis.from_url(cfg.REDIS_URL, decode_responses=True)
                ps = r.pubsub()
                ps.subscribe("config-updates")
                for msg in ps.listen():
                    if msg["type"] == "message":
                        data = json.loads(msg["data"])
                        if data.get("type") == "ZONE_UPDATE":
                            self._load_from_db()
            except Exception as e:
                logger.warning(f"[ZoneMatcher] Redis listener failed: {e}")

        threading.Thread(target=_listen, daemon=True, name="zone-listener").start()

    def match(self, cx_norm: float, cy_norm: float) -> Optional[dict]:
        from shapely.geometry import Point, Polygon
        pt = Point(cx_norm, cy_norm)
        with self._lock:
            for zone in self._zones.values():
                try:
                    if Polygon(zone["polygon_coords"]).contains(pt):
                        return zone
                except Exception:
                    pass
        if not self._zones:
            return {"id": "default", "name": "Full Frame", "polygon_coords": []}
        return None


# ══════════════════════════════════════════════════════════════════════════════
# DetectLoop — YOLO inference per camera
# ══════════════════════════════════════════════════════════════════════════════

class DetectLoop:
    def __init__(self, camera_id: str, frame_buffer: FrameBuffer,
                 bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                 zone_matcher: ZoneMatcher):
        self.camera_id    = camera_id
        self._buf         = frame_buffer
        self._bus_q       = bus_queue
        self._loop        = loop
        self._zones       = zone_matcher
        self._stop        = threading.Event()
        self._skip_count  = 0
        self._detect_count = 0

        from ultralytics import YOLO
        self._model       = YOLO(cfg.YOLO_MODEL)
        self._motion_gate = MotionGate(threshold=cfg.MOTION_THRESHOLD) if cfg.USE_MOTION_GATE else None
        self._dwell       = DwellTracker(
            on_event=self._emit_event,
            camera_id=camera_id,
            loiter_threshold_sec=cfg.LOITER_SEC,
        ) if cfg.USE_TRACKER else None
        self._frame_interval = 1.0 / cfg.DETECT_FPS

    def start(self) -> threading.Thread:
        t = threading.Thread(
            target=self._loop_fn, daemon=True,
            name=f"detect-{self.camera_id}",
        )
        t.start()
        return t

    def stop(self):
        self._stop.set()
        if self._dwell:
            self._dwell.flush_all()

    def health(self) -> dict:
        total = self._skip_count + self._detect_count
        return {
            "camera_id":       self.camera_id,
            "frames_skipped":  self._skip_count,
            "frames_detected": self._detect_count,
            "motion_gate_eff": round(self._skip_count / total, 2) if total else 0,
        }

    def _loop_fn(self):
        logger.info(f"[DetectLoop:{self.camera_id}] Starting…")
        while not self._stop.is_set():
            t0 = time.monotonic()
            cf = self._buf.get_latest()
            if cf is None:
                self._stop.wait(0.1)
                continue

            frame = cf.frame

            if self._motion_gate and not self._motion_gate.has_motion(frame):
                self._skip_count += 1
                elapsed = time.monotonic() - t0
                self._stop.wait(max(0, self._frame_interval - elapsed))
                continue

            self._detect_count += 1
            try:
                if cfg.USE_TRACKER:
                    results = self._model.track(
                        frame, persist=True, verbose=False,
                        conf=cfg.CONF_THRESHOLD, tracker="bytetrack.yaml",
                    )
                else:
                    results = self._model(frame, verbose=False, conf=cfg.CONF_THRESHOLD)
                self._process_results(frame, cf.timestamp, results)
            except Exception as e:
                logger.error(f"[DetectLoop:{self.camera_id}] Inference error: {e}")

            elapsed = time.monotonic() - t0
            self._stop.wait(max(0, self._frame_interval - elapsed))

    def _process_results(self, frame: np.ndarray, timestamp: float, results):
        h, w = frame.shape[:2]
        for box in (results[0].boxes or []):
            cls_id = int(box.cls[0]) if box.cls is not None else -1
            if cls_id not in cfg.DETECT_CLASSES:
                continue
            conf     = float(box.conf[0])
            x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())
            track_id = int(box.id[0]) if box.id is not None else None
            cx_norm  = ((x1 + x2) / 2) / w
            cy_norm  = ((y1 + y2) / 2) / h
            zone = self._zones.match(cx_norm, cy_norm)
            if zone is None:
                continue

            ev = {
                "event_id":     str(uuid.uuid4()),
                "camera_id":    self.camera_id,
                "timestamp":    timestamp,
                "object_class": cfg.DETECT_CLASSES[cls_id],
                "confidence":   round(conf, 3),
                "track_id":     track_id,
                "zone_id":      zone["id"],
                "zone_name":    zone["name"],
                "bbox":         {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            }
            if cfg.EMBED_FRAME:
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.FRAME_JPEG_Q])
                ev["trigger_frame_b64"] = base64.b64encode(buf).decode()

            if self._dwell and track_id is not None:
                self._dwell.update(track_id, zone["id"], zone["name"], ev)
            else:
                self._emit_event({**ev, "event_type": "DETECTED"})

    def _emit_event(self, event: dict):
        asyncio.run_coroutine_threadsafe(self._bus_q.put(event), self._loop)


# ══════════════════════════════════════════════════════════════════════════════
# CameraWorker — owns FrameBuffer + DetectLoop for one camera
# ══════════════════════════════════════════════════════════════════════════════

class CameraWorker:
    def __init__(self, camera_id: str, rtsp_url: str,
                 bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop,
                 zone_matcher: ZoneMatcher):
        self.camera_id = camera_id
        self._buf      = FrameBuffer(rtsp_url=rtsp_url, camera_id=camera_id,
                                     max_seconds=10, fps=cfg.DETECT_FPS)
        self._detect   = DetectLoop(camera_id, self._buf, bus_queue, loop, zone_matcher)
        self._recorder = None

        if cfg.RECORDINGS_ENABLED:
            try:
                from workers.recording_worker import CameraRecorder
                self._recorder = CameraRecorder(camera_id, rtsp_url, bus_queue, loop)
            except Exception as e:
                logger.warning(f"[CameraWorker:{camera_id}] Recorder unavailable: {e}")

    def start(self):
        self._buf.start()
        self._detect.start()
        if self._recorder:
            self._recorder.start()
        logger.info(f"[CameraWorker:{self.camera_id}] Started")

    def stop(self):
        self._detect.stop()
        self._buf.stop()
        if self._recorder:
            self._recorder.stop()

    def health(self) -> dict:
        return {**self._buf.health(), **self._detect.health(),
                "recording": self._recorder is not None}


# ══════════════════════════════════════════════════════════════════════════════
# Module API — called by api/server.py
# ══════════════════════════════════════════════════════════════════════════════

_camera_workers: dict[str, CameraWorker] = {}
_zone_matcher: Optional[ZoneMatcher]     = None


def start_cameras(bus_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    global _zone_matcher
    _zone_matcher = ZoneMatcher()
    for cam in cfg.CAMERAS:
        w = CameraWorker(cam["id"], cam["rtsp_url"], bus_queue, loop, _zone_matcher)
        w.start()
        _camera_workers[cam["id"]] = w
    logger.info(f"[EdgeWorker] Started {len(_camera_workers)} camera(s)")


def stop_cameras():
    for w in _camera_workers.values():
        w.stop()


def cameras_health() -> list[dict]:
    return [w.health() for w in _camera_workers.values()]
