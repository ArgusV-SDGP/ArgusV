"""
workers/watchdog_worker.py — Camera health watchdog
-----------------------------------------------------
Tasks: WATCH-03 to WATCH-07

Periodically checks Redis heartbeats.
If a camera's heartbeat TTL has expired → it's offline.
Attempts to restart the dead CameraWorker thread.
"""

import asyncio
import logging
import os
import shutil
import time
from collections import defaultdict

import config as cfg

logger = logging.getLogger("watchdog")

WATCHDOG_INTERVAL_SEC = 30
# How many consecutive missed heartbeats before we restart.
# At 30s interval, 2 misses = ~60s of silence before restarting.
_MISS_THRESHOLD = 2
_miss_counts: dict[str, int] = defaultdict(int)


async def watchdog_worker():
    """
    Runs every WATCHDOG_INTERVAL_SEC.
    Checks camera heartbeats and disk space.
    Task WATCH-03
    """
    logger.info("[Watchdog] Worker started")
    while True:
        try:
            await _check_cameras()
            await _check_disk()
            await _check_queue_depth()
        except Exception as e:
            logger.error(f"[Watchdog] Error: {e}", exc_info=True)
        await asyncio.sleep(WATCHDOG_INTERVAL_SEC)


async def _check_cameras():
    """
    Check Redis heartbeats for each configured camera.
    Requires _MISS_THRESHOLD consecutive missed beats before restart,
    so transient reconnects / H264 decode errors don't trigger spurious restarts.
    Task WATCH-02, WATCH-03
    """
    import redis as _redis
    r = _redis.from_url(cfg.REDIS_URL, decode_responses=True)
    from workers.edge_worker import _camera_workers
    for cam_id, worker in _camera_workers.items():
        alive = r.exists(f"camera:status:{cam_id}")
        if alive:
            _miss_counts[cam_id] = 0   # heartbeat present — reset counter
            continue

        _miss_counts[cam_id] += 1
        misses = _miss_counts[cam_id]

        # Also skip restart if the FrameBuffer got a good frame recently
        buf = getattr(worker, "_buf", None)
        last_good = getattr(buf, "_last_good_frame_ts", 0.0)
        if time.time() - last_good < WATCHDOG_INTERVAL_SEC * 1.5:
            logger.info(
                f"[Watchdog] Camera {cam_id} heartbeat miss #{misses} "
                f"but FrameBuffer got frame recently — skipping restart"
            )
            continue

        if misses < _MISS_THRESHOLD:
            logger.info(
                f"[Watchdog] Camera {cam_id} heartbeat miss #{misses}/{_MISS_THRESHOLD} — waiting"
            )
            continue

        logger.warning(
            f"[Watchdog] Camera {cam_id} offline ({misses} missed beats) — restarting thread"
        )
        _miss_counts[cam_id] = 0
        try:
            worker.stop()
        except Exception:
            pass
        worker.start()


async def _check_disk():
    """
    Warn when recordings directory exceeds threshold.
    Task WATCH-05
    """
    recordings_dir = os.getenv("LOCAL_RECORDINGS_DIR", "/recordings")
    if not os.path.exists(recordings_dir):
        logger.debug("[Watchdog] Recordings dir %s not found — skipping disk check", recordings_dir)
        return
    try:
        usage = shutil.disk_usage(recordings_dir)
        pct = (usage.used / usage.total) * 100
        if pct > cfg.WATCHDOG_DISK_WARN_PCT:
            logger.warning(
                "[Watchdog] Disk %.0f%% full (threshold %d%%) — cleanup needed",
                pct, cfg.WATCHDOG_DISK_WARN_PCT,
            )
        else:
            logger.debug("[Watchdog] Disk usage %.0f%% — OK", pct)
    except Exception as e:
        logger.warning(f"[Watchdog] Disk check failed: {e}")


async def _check_queue_depth():
    """
    Log warning when bus queues >80% capacity.
    Task WATCH-06
    """
    from bus import bus
    stats = bus.stats()
    thresholds = {
        "raw_detections": 800,   # maxsize=1000
        "vlm_requests":   160,   # maxsize=200
    }
    for q, limit in thresholds.items():
        size = stats.get(q, 0)
        if size > limit:
            logger.warning(f"[Watchdog] Bus queue '{q}' at {size} (>{limit}) — possible backpressure")
