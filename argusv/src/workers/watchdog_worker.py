"""
workers/watchdog_worker.py — System & Camera Watchdog
------------------------------------------------------
Tasks: WATCH-02, WATCH-03, WATCH-05

Monitors:
  1. Camera connectivity (via EdgeWorker health)
  2. Background task responsiveness (heartbeats)
  3. System resources

Action:
  - If a camera is offline, signal restart.
  - If a task is hung, log CRITICAL and/or signal restart.
"""

import asyncio
import logging
import time
from typing import Dict, List

from workers.edge_worker import cameras_health, start_cameras, stop_cameras
from bus import bus
import config as cfg

logger = logging.getLogger("watchdog")

# Settings
WATCHDOG_INTERVAL_SEC = 10
CAMERA_RESTART_THRESHOLD_SEC = 30  # Wait this long before giving up and restarting

# Internal state
_last_restart_attempt: Dict[str, float] = {}
_task_heartbeats: Dict[str, float] = {}


def record_heartbeat(task_name: str):
    """Called by workers to signal they are alive."""
    _task_heartbeats[task_name] = time.time()


async def watchdog_worker():
    """Main watchdog loop."""
    logger.info("[Watchdog] Worker started")
    
    while True:
        try:
            # 1. Check Camera Health (WATCH-02, WATCH-05)
            await _check_cameras()
            
            # 2. Check Task Heartbeats (WATCH-03)
            _check_tasks()
            
        except Exception as e:
            logger.error(f"[Watchdog] Loop error: {e}")
            
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
def _check_tasks():
    """Ensure background workers are still checking in."""
    now = time.time()
    STALE_THRESHOLD = 60 # 1 minute
    
    expected_tasks = [
        "stream-ingestion",
        "vlm-inference",
        "decision-engine",
        "notification"
    ]
    
    for task in expected_tasks:
        last_hb = _task_heartbeats.get(task)
        if last_hb is None:
            # Task hasn't reported even once
            continue
            
        if now - last_hb > STALE_THRESHOLD:
            logger.critical(f"[Watchdog] Task '{task}' is STALE (last heartbeat {round(now-last_hb)}s ago)!")
            # Future: add logic to cancel and recreate the asyncio task
