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
    Check Redis heartbeats for each configured camera.
    If offline for >60s → attempt restart.
    Task WATCH-02, WATCH-03
    """
    import redis as _redis
    r = _redis.from_url(cfg.REDIS_URL, decode_responses=True)
    from workers.edge_worker import _camera_workers
    for cam_id, worker in _camera_workers.items():
        # Redis key format from the CameraWorker
        alive = r.exists(f"camera:status:{cam_id}")
        if not alive:
            logger.warning(f"[Watchdog] Camera {cam_id} offline — restarting thread")
            try:
                worker.stop()
            except Exception:
                pass
            worker.start()


async def _check_disk():
    """
    Warn when recordings directory >80% full.
    Task WATCH-05
    """
    import shutil
    import os
    
    # Use the same path as cleanup worker
    recordings_dir = cfg.LOCAL_RECORDINGS_DIR
    if os.path.exists(recordings_dir):
        usage = shutil.disk_usage(recordings_dir)
        pct = usage.used / usage.total
        if pct > 0.8:
            logger.warning(f"[Watchdog] Disk usage at {pct:.0%} for {recordings_dir} — cleanup needed")


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
