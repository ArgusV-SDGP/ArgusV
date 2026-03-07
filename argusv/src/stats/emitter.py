"""
stats/emitter.py — System statistics collector
------------------------------------------------
Frigate equivalent: frigate/stats/emitter.py + util.py + prometheus.py

Collects and exposes runtime statistics:
  - Detection rates per camera
  - Inference latency
  - Queue depths
  - Storage usage
  - Process CPU/RAM

Exposes via:
  GET /health  (already done)
  GET /api/stats  (TODO API-27)
  GET /metrics (Prometheus, TODO WATCH-08)
"""

import asyncio
import logging
import os
import time
import psutil
from pathlib import Path
from typing import dict

logger = logging.getLogger("stats")

_stats: dict = {
    "detections_total":    0,
    "detections_per_cam":  {},
    "vlm_calls":           0,
    "vlm_latency_avg_ms":  0,
    "alerts_sent":         0,
    "uptime_sec":          0,
}

_start_time = time.time()
_vlm_latencies: list[float] = []


def record_detection(camera_id: str):
    _stats["detections_total"] += 1
    _stats["detections_per_cam"][camera_id] = \
        _stats["detections_per_cam"].get(camera_id, 0) + 1


def record_vlm_call(latency_ms: float):
    _stats["vlm_calls"] += 1
    _vlm_latencies.append(latency_ms)
    if len(_vlm_latencies) > 100:
        _vlm_latencies.pop(0)
    _stats["vlm_latency_avg_ms"] = sum(_vlm_latencies) / len(_vlm_latencies)


def record_alert():
    _stats["alerts_sent"] += 1


def get_stats() -> dict:
    """Full stats snapshot for /api/stats endpoint."""
    proc = psutil.Process(os.getpid())
    mem  = proc.memory_info()

    # Disk usage for recordings
    recordings_dir = os.getenv("LOCAL_RECORDINGS_DIR", "/recordings")
    disk = {}
    if Path(recordings_dir).exists():
        usage = psutil.disk_usage(recordings_dir)
        disk  = {
            "total_gb": round(usage.total / 1e9, 1),
            "used_gb":  round(usage.used  / 1e9, 1),
            "free_gb":  round(usage.free  / 1e9, 1),
            "pct":      usage.percent,
        }

    return {
        **_stats,
        "uptime_sec":   round(time.time() - _start_time, 1),
        "cpu_pct":      proc.cpu_percent(interval=0.1),
        "rss_mb":       round(mem.rss / 1e6, 1),
        "disk":         disk,
    }


async def stats_emitter_worker():
    """
    Background task: logs key stats every 60s.
    TODO WATCH-08: also emit to Prometheus /metrics
    """
    logger.info("[Stats] Emitter started")
    while True:
        await asyncio.sleep(60)
        s = get_stats()
        logger.info(
            f"[Stats] uptime={s['uptime_sec']}s "
            f"detections={s['detections_total']} "
            f"vlm_calls={s['vlm_calls']} "
            f"vlm_avg={s['vlm_latency_avg_ms']:.0f}ms "
            f"alerts={s['alerts_sent']} "
            f"rss={s['rss_mb']}MB"
        )
