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

import os
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
from sqlalchemy import create_engine, text
from shapely.geometry import Point, Polygon
from ultralytics import YOLO
import redis

import config as cfg
from bus import bus
from const import DEFAULT_LOITER_SEC, REDIS_ZONE_CHANNEL

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
        self._bad_frame_count = 0
        self._max_bad_frames  = 10   # tolerate up to 10 consecutive decode errors
        self._last_good_frame_ts: float = 0.0

        # Optimization: Set RTSP options globally once if needed, or via env
        # before opening the capture. 
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|buffer_size;10485760|threads;1"
        )
        os.environ["OPENCV_FFMPEG_THREADS"] = "1"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
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
            self._thread = None
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._cap = None
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
                    self._cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG if hasattr(cv2, 'CAP_FFMPEG') else cv2.CAP_ANY)
                    self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
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
            try:
                ret, frame = self._cap.read()
            except cv2.error as e:
                logger.warning(
                    f"[FrameBuffer:{self.camera_id}] cv2 error during read: {e} — reconnecting"
                )
                self._connected = False
                self._bad_frame_count = 0
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._stop.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                continue
            except Exception as e:
                logger.error(
                    f"[FrameBuffer:{self.camera_id}] Unexpected read error: {e} — reconnecting"
                )
                self._connected = False
                self._bad_frame_count = 0
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._stop.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                continue

            if not ret or frame is None:
                self._bad_frame_count += 1
                if self._bad_frame_count >= self._max_bad_frames:
                    logger.warning(
                        f"[FrameBuffer:{self.camera_id}] "
                        f"{self._bad_frame_count} consecutive bad frames — reconnecting"
                    )
                    self._connected = False
                    self._bad_frame_count = 0
                    if self._cap:
                        self._cap.release()
                else:
                    logger.debug(
                        f"[FrameBuffer:{self.camera_id}] Bad frame "
                        f"({self._bad_frame_count}/{self._max_bad_frames}), skipping"
                    )
                continue

            # good frame — reset error counter
            self._bad_frame_count = 0
            self._last_good_frame_ts = time.time()
            cf = CapturedFrame(
                frame=frame,
                timestamp=self._last_good_frame_ts,
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
        # Resize for faster background subtraction if high res
        # fg = self._bg_sub.apply(cv2.resize(frame, (640, 360)))
        fg = self._bg_sub.apply(frame)
        self._frame_count += 1
        if self._frame_count <= self._warmup_frames:
            return True
        return (np.count_nonzero(fg) / fg.size) > self._threshold


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

        self._thread = threading.Thread(
            target=self._eviction_loop, daemon=True,
            name=f"dwell-{camera_id}",
        )
        self._thread.start()

    def update(
        self,
        track_id: int,
        zone_id: str,
        zone_name: str,
        event: dict,
        loiter_threshold_sec: Optional[float] = None,
    ):
        now = time.time()
        effective_loiter_sec = float(loiter_threshold_sec or self._loiter_sec)
        with self._lock:
            if track_id not in self._tracks:
                self._tracks[track_id] = {
                    "first_seen":         now,
                    "last_seen":          now,
                    "last_update_emit":   now,
                    "loitering_emitted":  False,
                    "loiter_sec":         effective_loiter_sec,
                    "base_event":         event,
                }
                # Ensure zone_id and zone_name are updated in the event
                start_ev = {**event, "event_type": "START", "dwell_sec": 0, "zone_id": zone_id, "zone_name": zone_name}
                self._on_event(start_ev)
                return

            t = self._tracks[track_id]
            t["last_seen"] = now
            dwell = now - t["first_seen"]

            # Update base event context for future emits
            t["base_event"].update({
                "zone_id": zone_id,
                "zone_name": zone_name,
                "bbox": event.get("bbox", t["base_event"].get("bbox"))
            })
            t["loiter_sec"] = effective_loiter_sec

            if dwell >= t["loiter_sec"] and not t["loitering_emitted"]:
                t["loitering_emitted"] = True
                self._on_event({**t["base_event"], "event_type": "LOITERING", "dwell_sec": round(dwell, 1)})
            elif now - t["last_update_emit"] >= self._update_sec:
                t["last_update_emit"] = now
                self._on_event({**t["base_event"], "event_type": "UPDATE",    "dwell_sec": round(dwell, 1)})

    def flush_all(self):
        self._stop.set()
        with self._lock:
            for tid, t in self._tracks.items():
                dwell = time.time() - t["first_seen"]
                self._on_event({**t["base_event"], "event_type": "END", "dwell_sec": round(dwell, 1)})
            self._tracks.clear()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

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
        self._polygon_cache: dict[str, Polygon] = {}
        self._camera_zone_map: dict[str, set[str]] = {}
        self._stats = {
            "matched": 0,
            "outside_dropped": 0,
            "fallback_full_frame": 0,
            "hot_reload_events": 0,
            "db_reload_count": 0,
            "redis_reconnects": 0,
        }
        self._lock = threading.Lock()
        self._load_from_db()
        self._start_redis_listener()
        self._start_periodic_resync()

    def _load_from_db(self):
        try:
            engine = create_engine(cfg.POSTGRES_URL)
            with engine.connect() as conn:
                try:
                    rows = conn.execute(
                        text("SELECT zone_id, camera_id, name, polygon_coords, dwell_threshold_sec, active FROM zones")
                    ).fetchall()
                except Exception:
                    # Backward compatibility before zones.camera_id migration is applied.
                    rows = conn.execute(
                        text("SELECT zone_id, NULL as camera_id, name, polygon_coords, dwell_threshold_sec, active FROM zones")
                    ).fetchall()
            
            new_zones = {}
            new_poly_cache = {}
            new_camera_map: dict[str, set[str]] = {}
            for r in rows:
                if not r.active:
                    continue
                z_id = str(r.zone_id)
                camera_id = str(r.camera_id) if r.camera_id else None
                coords = (
                    json.loads(r.polygon_coords)
                    if isinstance(r.polygon_coords, str)
                    else r.polygon_coords
                )
                new_zones[z_id] = {
                    "id": z_id,
                    "camera_id": camera_id,
                    "name": r.name,
                    "polygon_coords": coords,
                    "dwell_threshold_sec": int(r.dwell_threshold_sec or DEFAULT_LOITER_SEC),
                }
                try:
                    if coords and len(coords) >= 3:
                        new_poly_cache[z_id] = Polygon(coords)
                except Exception as e:
                    logger.error(f"[ZoneMatcher] Error creating polygon for zone {z_id}: {e}")
                if camera_id:
                    new_camera_map.setdefault(camera_id, set()).add(z_id)

            with self._lock:
                self._zones = new_zones
                self._polygon_cache = new_poly_cache
                self._camera_zone_map = new_camera_map
                self._stats["db_reload_count"] += 1

            logger.info(
                f"[ZoneMatcher] Loaded {len(self._zones)} zones and "
                f"{sum(len(v) for v in self._camera_zone_map.values())} scoped zone bindings from DB"
            )
        except Exception as e:
            logger.warning(f"[ZoneMatcher] DB load failed (no zones mode): {e}")

    def _start_redis_listener(self):
        def _listen():
            backoff_sec = 1.0
            while True:
                try:
                    r  = redis.from_url(cfg.REDIS_URL, decode_responses=True)
                    ps = r.pubsub()
                    ps.subscribe(REDIS_ZONE_CHANNEL)
                    backoff_sec = 1.0
                    for msg in ps.listen():
                        if msg["type"] != "message":
                            continue
                        try:
                            data = json.loads(msg["data"])
                            if data.get("type") == "ZONE_UPDATE":
                                self._apply_zone_update(data)
                        except Exception as e:
                            logger.error(f"[ZoneMatcher] Redis message parse error: {e}")
                except Exception as e:
                    with self._lock:
                        self._stats["redis_reconnects"] += 1
                    logger.warning(
                        f"[ZoneMatcher] Redis listener failed: {e} (retry in {backoff_sec:.0f}s)"
                    )
                    time.sleep(backoff_sec)
                    backoff_sec = min(backoff_sec * 2, 30.0)

        threading.Thread(target=_listen, daemon=True, name="zone-listener").start()

    def _start_periodic_resync(self):
        interval = max(10, int(getattr(cfg, "ZONE_RESYNC_SEC", 60)))

        def _resync():
            while True:
                time.sleep(interval)
                self._load_from_db()

        threading.Thread(target=_resync, daemon=True, name="zone-resync").start()

    def _apply_zone_update(self, data: dict):
        action = data.get("action")
        zone_id = data.get("zone_id")
        zone = data.get("zone")

        with self._lock:
            self._stats["hot_reload_events"] += 1

        if not zone_id:
            self._load_from_db()
            return

        with self._lock:
            if action == "deleted":
                self._zones.pop(zone_id, None)
                self._polygon_cache.pop(zone_id, None)
                logger.info(f"[ZoneMatcher] Hot-reloaded delete for zone={zone_id}")
                threading.Thread(target=self._load_from_db, daemon=True).start()
            elif action in {"created", "updated"} and isinstance(zone, dict):
                if zone.get("active", True):
                    coords = zone.get("polygon_coords", [])
                    camera_id = zone.get("camera_id")
                    self._zones[zone_id] = {
                        "id": zone_id,
                        "camera_id": camera_id,
                        "name": zone.get("name", ""),
                        "polygon_coords": coords,
                        "dwell_threshold_sec": int(zone.get("dwell_threshold_sec") or DEFAULT_LOITER_SEC),
                    }
                    try:
                        if coords and len(coords) >= 3:
                            self._polygon_cache[zone_id] = Polygon(coords)
                    except Exception as e:
                        logger.error(f"[ZoneMatcher] Error updating polygon for {zone_id}: {e}")
                else:
                    self._zones.pop(zone_id, None)
                    self._polygon_cache.pop(zone_id, None)
                logger.info(f"[ZoneMatcher] Hot-reloaded {action} for zone={zone_id}")
                threading.Thread(target=self._load_from_db, daemon=True).start()
            else:
                # Fallback to full reload for unexpected formats
                threading.Thread(target=self._load_from_db, daemon=True).start()

    def match(self, cx_norm: float, cy_norm: float, camera_id: Optional[str] = None) -> Optional[dict]:
        pt = Point(cx_norm, cy_norm)
        with self._lock:
            camera_zone_ids = self._camera_zone_map.get(camera_id, set()) if camera_id else set()

            # If camera has scoped zones, enforce that scope.
            if camera_zone_ids:
                for z_id in camera_zone_ids:
                    poly = self._polygon_cache.get(z_id)
                    if poly is None:
                        continue
                    try:
                        if poly.covers(pt):
                            self._stats["matched"] += 1
                            return self._zones.get(z_id)
                    except Exception:
                        continue
                self._stats["outside_dropped"] += 1
                return None

            # Check cached polygons first (O(N) but shapely is fast)
            for z_id, poly in self._polygon_cache.items():
                try:
                    if poly.covers(pt):
                        self._stats["matched"] += 1
                        return self._zones[z_id]
                except Exception:
                    pass
            
            # Fallback if no zones defined
            if not self._zones:
                self._stats["fallback_full_frame"] += 1
                return {
                    "id": "default",
                    "name": "Full Frame",
                    "polygon_coords": [],
                    "dwell_threshold_sec": DEFAULT_LOITER_SEC,
                }
            self._stats["outside_dropped"] += 1
        return None

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)


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

        self._model       = YOLO(cfg.YOLO_MODEL)
        self._motion_gate = MotionGate(threshold=cfg.MOTION_THRESHOLD) if cfg.USE_MOTION_GATE else None
        self._dwell       = DwellTracker(
            on_event=self._emit_event,
            camera_id=camera_id,
            loiter_threshold_sec=cfg.LOITER_SEC,
            update_interval_sec=cfg.TRACK_UPDATE_SEC,
            evict_after_sec=cfg.TRACK_EVICT_SEC,
        ) if cfg.USE_TRACKER else None
        self._frame_interval = 1.0 / cfg.DETECT_FPS
        self._thread: Optional[threading.Thread] = None

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        if self._dwell is None and cfg.USE_TRACKER:
            self._dwell = DwellTracker(
                on_event=self._emit_event,
                camera_id=self.camera_id,
                loiter_threshold_sec=cfg.LOITER_SEC,
                update_interval_sec=cfg.TRACK_UPDATE_SEC,
                evict_after_sec=cfg.TRACK_EVICT_SEC,
            )
        t = threading.Thread(
            target=self._loop_fn, daemon=True,
            name=f"detect-{self.camera_id}",
        )
        t.start()
        self._thread = t
        return t

    def stop(self):
        self._stop.set()
        if self._dwell:
            self._dwell.flush_all()
            self._dwell = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

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
                
                if results and len(results) > 0:
                    self._process_results(frame, cf.timestamp, results[0])
            except Exception as e:
                logger.error(f"[DetectLoop:{self.camera_id}] Inference error: {e}", exc_info=True)

            elapsed = time.monotonic() - t0
            self._stop.wait(max(0, self._frame_interval - elapsed))

    def _process_results(self, frame: np.ndarray, timestamp: float, result):
        h, w = frame.shape[:2]
        if not result.boxes:
            return

        for box in result.boxes:
            cls_id = int(box.cls[0]) if box.cls is not None else -1
            if cls_id not in cfg.DETECT_CLASSES:
                continue
            
            conf     = float(box.conf[0])
            xyxy     = box.xyxy[0].tolist()
            x1, y1, x2, y2 = map(int, xyxy)
            
            track_id = int(box.id[0]) if box.id is not None else None
            cx_norm  = ((x1 + x2) / 2) / w
            cy_norm  = ((y1 + y2) / 2) / h
            
            zone = self._zones.match(cx_norm, cy_norm, camera_id=self.camera_id)
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
                "frame_w":      w,
                "frame_h":      h,
            }
            
            if cfg.EMBED_FRAME:
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.FRAME_JPEG_Q])
                ev["trigger_frame_b64"] = base64.b64encode(buf).decode()

            if self._dwell and track_id is not None:
                self._dwell.update(
                    track_id,
                    zone["id"],
                    zone["name"],
                    ev,
                    loiter_threshold_sec=zone.get("dwell_threshold_sec", cfg.LOITER_SEC),
                )
            else:
                self._emit_event({**ev, "event_type": "DETECTED"})

    def _emit_event(self, event: dict):
        if self._loop.is_running():
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

        logger.info(f"[CameraWorker:{camera_id}] RECORDINGS_ENABLED={cfg.RECORDINGS_ENABLED}")
        if cfg.RECORDINGS_ENABLED:
            try:
                from workers.recording_worker import CameraRecorder
                self._recorder = CameraRecorder(camera_id, rtsp_url, bus.segments, loop)
                logger.info(f"[CameraWorker:{camera_id}] ✅ Recorder initialized")
            except Exception as e:
                logger.error(f"[CameraWorker:{camera_id}] ❌ Recorder initialization failed: {e}", exc_info=True)

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
    zone_stats = _zone_matcher.stats() if _zone_matcher else {}
    return [{**w.health(), "zone_matcher": zone_stats} for w in _camera_workers.values()]
