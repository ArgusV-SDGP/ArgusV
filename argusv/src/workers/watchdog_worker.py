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
import time

import config as cfg

logger = logging.getLogger("watchdog")

WATCHDOG_INTERVAL_SEC = 30


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
    Check camera health by checking buffer size and frame count.
    If no frames captured in 60s → attempt restart.
    Task WATCH-02, WATCH-03
    """
    from workers.edge_worker import _camera_workers
    for cam_id, worker in _camera_workers.items():
        health = worker.health()

        # Check if camera is actually connected and receiving frames
        is_connected = health.get("connected", False)
        frame_count = health.get("frame_count", 0)

        # Only restart if truly offline (not connected AND no frames)
        if not is_connected and frame_count == 0:
            logger.warning(f"[Watchdog] Camera {cam_id} offline — restarting thread")
            try:
                worker.stop()
            except Exception:
                pass
            worker.start()
        elif is_connected:
            # Camera is healthy - log debug info
            logger.debug(f"[Watchdog] Camera {cam_id} healthy: {frame_count} frames, buffer={health.get('buffer_size', 0)}")


async def _check_disk():
    """
    Warn when recordings directory >80% full OR >10GB used.
    Task WATCH-05
    """
    import shutil
    import os

    # Use the same path as cleanup worker
    recordings_dir = cfg.LOCAL_RECORDINGS_DIR
    if os.path.exists(recordings_dir):
        usage = shutil.disk_usage(recordings_dir)
        pct = usage.used / usage.total
        used_gb = usage.used / (1024**3)

        # Only warn if both conditions met: >80% full AND >1GB used
        # This prevents false positives on small partitions
        if pct > 0.8 and used_gb > 1.0:
            logger.warning(f"[Watchdog] Disk usage at {pct:.0%} ({used_gb:.1f}GB used) for {recordings_dir} — cleanup needed")
        elif used_gb > 10.0:
            logger.info(f"[Watchdog] Recordings using {used_gb:.1f}GB — consider cleanup")


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
