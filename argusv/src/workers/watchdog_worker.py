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
    Check camera health by checking buffer state and frame activity.
    Requires _MISS_THRESHOLD consecutive missed beats before restart,
    so transient reconnects / H264 decode errors don't trigger spurious restarts.
    Task WATCH-02, WATCH-03
    """
    from workers.edge_worker import _camera_workers
    for cam_id, worker in _camera_workers.items():
        health = worker.health()

        # Check if camera is actually connected and receiving frames
        is_connected = health.get("connected", False)
        frame_count = health.get("frame_count", 0)

        # Also check last good frame timestamp from FrameBuffer
        buf = getattr(worker, "_buf", None)
        last_good = getattr(buf, "_last_good_frame_ts", 0.0)

        # If camera got frame recently, it's healthy
        if time.time() - last_good < WATCHDOG_INTERVAL_SEC * 1.5:
            _miss_counts[cam_id] = 0
            logger.debug(f"[Watchdog] Camera {cam_id} healthy: {frame_count} frames, buffer={health.get('buffer_size', 0)}")
            continue

        # If connected but no recent frames, increment miss counter
        if not is_connected and frame_count == 0:
            _miss_counts[cam_id] += 1
            misses = _miss_counts[cam_id]

            if misses < _MISS_THRESHOLD:
                logger.info(
                    f"[Watchdog] Camera {cam_id} offline #{misses}/{_MISS_THRESHOLD} — waiting"
                )
                continue

            # Threshold reached - restart
            logger.warning(
                f"[Watchdog] Camera {cam_id} offline ({misses} consecutive checks) — restarting thread"
            )
            _miss_counts[cam_id] = 0
            try:
                worker.stop()
            except Exception:
                pass
            worker.start()
        else:
            # Camera is connected, reset counter
            _miss_counts[cam_id] = 0


async def _check_disk():
    """
    Warn when recordings directory >80% full OR >10GB used.
    Warn when recordings directory exceeds threshold.
    Task WATCH-05
    """
    import shutil
    import os

    # Use the same path as cleanup worker
    recordings_dir = cfg.LOCAL_RECORDINGS_DIR
    if not os.path.exists(recordings_dir):
        logger.debug("[Watchdog] Recordings dir %s not found — skipping disk check", recordings_dir)
        return
    try:
        usage = shutil.disk_usage(recordings_dir)
        pct = usage.used / usage.total
        used_gb = usage.used / (1024**3)

        # Only warn if both conditions met: >80% full AND >1GB used
        # This prevents false positives on small partitions
        if pct > 0.8 and used_gb > 1.0:
            logger.warning(f"[Watchdog] Disk usage at {pct:.0%} ({used_gb:.1f}GB used) for {recordings_dir} — cleanup needed")
        elif used_gb > 10.0:
            logger.info(f"[Watchdog] Recordings using {used_gb:.1f}GB — consider cleanup")
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
