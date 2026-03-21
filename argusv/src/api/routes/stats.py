"""
api/routes/stats.py — System Stats and Metrics
-----------------------------------------------
Task: API-27

Provides runtime statistics about:
- Queue depths
- Camera health
- Detection counts
- Incident counts by threat level
- Disk usage
- Processing latency metrics
"""

import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from typing import Any

import config as cfg
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from bus import bus
from db.connection import get_db
from db.models import Camera, Detection, Incident, Segment

router = APIRouter(prefix="/api/stats", tags=["stats"])
logger = logging.getLogger("api.stats")

# Imported lazily to avoid circular imports at module load time
def _get_app_started() -> float:
    try:
        from api.server import APP_STARTED_AT
        return APP_STARTED_AT
    except ImportError:
        return time.time()

_app_started: float = time.time()  # fallback; overwritten on first request


@router.get("")
def get_system_stats(
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
) -> dict[str, Any]:
    """
    Returns comprehensive system statistics including:
    - Queue depths and bus health
    - Camera online status
    - Detection and incident counts (24h window)
    - Disk usage for recordings
    - Latest incident summary
    """
    global _app_started
    _app_started = _get_app_started()

    # 1. Bus queue stats
    bus_stats = bus.stats()
    queue_health = {
        "raw_detections": {
            "size": bus_stats.get("raw_detections", 0),
            "maxsize": 1000,
            "utilization_pct": round(bus_stats.get("raw_detections", 0) / 10, 1),
        },
        "vlm_requests": {
            "size": bus_stats.get("vlm_requests", 0),
            "maxsize": 200,
            "utilization_pct": round(bus_stats.get("vlm_requests", 0) / 2, 1),
        },
        "vlm_results": {
            "size": bus_stats.get("vlm_results", 0),
            "maxsize": 200,
            "utilization_pct": round(bus_stats.get("vlm_results", 0) / 2, 1),
        },
        "actions": {
            "size": bus_stats.get("actions", 0),
            "maxsize": 500,
            "utilization_pct": round(bus_stats.get("actions", 0) / 5, 1),
        },
    }

    # 2. Camera health
    cameras = db.query(Camera).all()
    camera_health = [
        {
            "camera_id": cam.camera_id,
            "name": cam.name,
            "status": cam.status,
            "resolution": cam.resolution,
            "fps": cam.fps,
        }
        for cam in cameras
    ]

    # 3. Detection stats (last 24h)
    last_24h = datetime.utcnow() - timedelta(hours=24)
    total_detections = db.query(func.count(Detection.detection_id)).filter(
        Detection.detected_at >= last_24h
    ).scalar() or 0

    threat_detections = db.query(func.count(Detection.detection_id)).filter(
        Detection.detected_at >= last_24h,
        Detection.is_threat == True,
    ).scalar() or 0

    # 4. Incident stats (all time + 24h)
    total_incidents = db.query(func.count(Incident.incident_id)).scalar() or 0
    open_incidents = db.query(func.count(Incident.incident_id)).filter(
        Incident.status == "OPEN"
    ).scalar() or 0

    incidents_24h = db.query(func.count(Incident.incident_id)).filter(
        Incident.detected_at >= last_24h
    ).scalar() or 0

    incidents_by_threat = (
        db.query(Incident.threat_level, func.count(Incident.incident_id))
        .filter(Incident.detected_at >= last_24h)
        .group_by(Incident.threat_level)
        .all()
    )
    threat_breakdown = {level: count for level, count in incidents_by_threat}

    # 5. Recording/segment stats
    total_segments = db.query(func.count(Segment.segment_id)).scalar() or 0
    total_storage_bytes = db.query(func.sum(Segment.size_bytes)).scalar() or 0
    total_storage_gb = round(total_storage_bytes / (1024**3), 2)

    # 6. Disk usage
    disk_usage = None
    recordings_dir = cfg.LOCAL_RECORDINGS_DIR
    if os.path.exists(recordings_dir):
        usage = shutil.disk_usage(recordings_dir)
        disk_usage = {
            "total_gb": round(usage.total / (1024**3), 2),
            "used_gb": round(usage.used / (1024**3), 2),
            "free_gb": round(usage.free / (1024**3), 2),
            "used_pct": round((usage.used / usage.total) * 100, 1),
        }

    # 7. Latest incident
    latest_incident = db.query(Incident).order_by(Incident.detected_at.desc()).first()
    latest_incident_summary = None
    if latest_incident:
        latest_incident_summary = {
            "incident_id": str(latest_incident.incident_id),
            "camera_id": latest_incident.camera_id,
            "threat_level": latest_incident.threat_level,
            "summary": latest_incident.summary,
            "detected_at": latest_incident.detected_at.isoformat(),
            "status": latest_incident.status,
        }

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "uptime_info": {
            "app_started": datetime.utcfromtimestamp(_app_started).isoformat(),
            "uptime_seconds": round(time.time() - _app_started),
        },
        "bus": {
            "queue_health": queue_health,
            "total_size": sum(q["size"] for q in queue_health.values()),
        },
        "cameras": {
            "total": len(cameras),
            "online": sum(1 for c in cameras if c.status == "online"),
            "offline": sum(1 for c in cameras if c.status == "offline"),
            "health": camera_health,
        },
        "detections": {
            "last_24h": total_detections,
            "threats_24h": threat_detections,
            "threat_rate_pct": round((threat_detections / total_detections * 100) if total_detections > 0 else 0, 1),
        },
        "incidents": {
            "total": total_incidents,
            "open": open_incidents,
            "last_24h": incidents_24h,
            "by_threat_level": threat_breakdown,
            "latest": latest_incident_summary,
        },
        "recordings": {
            "total_segments": total_segments,
            "total_storage_gb": total_storage_gb,
            "disk_usage": disk_usage,
        },
        "system": {
            "recordings_enabled": cfg.RECORDINGS_ENABLED,
            "detect_fps": cfg.DETECT_FPS,
            "conf_threshold": cfg.CONF_THRESHOLD,
            "use_tiered_vlm": cfg.USE_TIERED_VLM,
        },
    }
