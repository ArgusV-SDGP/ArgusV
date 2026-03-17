"""
api/routes/metrics.py — Prometheus Metrics Endpoint
----------------------------------------------------
Task: WATCH-08

Exposes Prometheus-compatible metrics at /metrics endpoint.

Metrics exposed:
- argusv_detections_total (counter) - Total detections processed
- argusv_incidents_total (counter) - Total incidents created
- argusv_queue_size (gauge) - Size of each bus queue
- argusv_camera_online (gauge) - Camera online status (1=online, 0=offline)
- argusv_disk_usage_bytes (gauge) - Disk usage for recordings
- argusv_segment_count (gauge) - Total video segments stored
"""

import logging
import os
import shutil
from typing import Any

import config as cfg
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from bus import bus
from db.connection import get_db
from db.models import Camera, Detection, Incident, Segment

router = APIRouter(tags=["metrics"])
logger = logging.getLogger("api.metrics")


def _format_prometheus(metrics: list[tuple[str, dict[str, str], float, str]]) -> str:
    """
    Format metrics in Prometheus text format.

    Args:
        metrics: List of (metric_name, labels_dict, value, help_text) tuples

    Returns:
        Prometheus-formatted text
    """
    lines = []

    for metric_name, labels, value, help_text in metrics:
        # Add HELP line
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} gauge")

        # Format labels
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{metric_name}{{{label_str}}} {value}")
        else:
            lines.append(f"{metric_name} {value}")

    return "\n".join(lines) + "\n"


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(db: Session = Depends(get_db)) -> Response:
    """
    Prometheus-compatible metrics endpoint.
    No authentication required (prometheus scraper doesn't support JWT easily).
    """

    metrics: list[tuple[str, dict[str, str], float, str]] = []

    # 1. Total detections
    total_detections = db.query(func.count(Detection.detection_id)).scalar() or 0
    metrics.append((
        "argusv_detections_total",
        {},
        float(total_detections),
        "Total number of detections processed"
    ))

    # 2. Total incidents
    total_incidents = db.query(func.count(Incident.incident_id)).scalar() or 0
    metrics.append((
        "argusv_incidents_total",
        {},
        float(total_incidents),
        "Total number of incidents created"
    ))

    # 3. Open incidents
    open_incidents = db.query(func.count(Incident.incident_id)).filter(
        Incident.status == "OPEN"
    ).scalar() or 0
    metrics.append((
        "argusv_incidents_open",
        {},
        float(open_incidents),
        "Number of open incidents"
    ))

    # 4. Queue sizes
    bus_stats = bus.stats()
    for queue_name, size in bus_stats.items():
        metrics.append((
            "argusv_queue_size",
            {"queue": queue_name},
            float(size),
            "Size of event bus queue"
        ))

    # 5. Camera online status
    cameras = db.query(Camera).all()
    for cam in cameras:
        metrics.append((
            "argusv_camera_online",
            {"camera_id": cam.camera_id, "name": cam.name or cam.camera_id},
            1.0 if cam.status == "online" else 0.0,
            "Camera online status (1=online, 0=offline)"
        ))

    # 6. Total segments
    total_segments = db.query(func.count(Segment.segment_id)).scalar() or 0
    metrics.append((
        "argusv_segments_total",
        {},
        float(total_segments),
        "Total number of video segments stored"
    ))

    # 7. Disk usage
    recordings_dir = cfg.LOCAL_RECORDINGS_DIR
    if os.path.exists(recordings_dir):
        usage = shutil.disk_usage(recordings_dir)
        metrics.append((
            "argusv_disk_usage_bytes",
            {"path": recordings_dir, "type": "total"},
            float(usage.total),
            "Total disk space"
        ))
        metrics.append((
            "argusv_disk_usage_bytes",
            {"path": recordings_dir, "type": "used"},
            float(usage.used),
            "Used disk space"
        ))
        metrics.append((
            "argusv_disk_usage_bytes",
            {"path": recordings_dir, "type": "free"},
            float(usage.free),
            "Free disk space"
        ))

    # 8. Total storage bytes from segments
    total_storage_bytes = db.query(func.sum(Segment.size_bytes)).scalar() or 0
    metrics.append((
        "argusv_segments_storage_bytes",
        {},
        float(total_storage_bytes),
        "Total storage used by video segments"
    ))

    # Return Prometheus text format
    return Response(
        content=_format_prometheus(metrics),
        media_type="text/plain; version=0.0.4",
    )
