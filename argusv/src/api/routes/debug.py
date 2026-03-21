"""
api/routes/debug.py — DB inspection endpoint for debugging
-----------------------------------------------------------
GET /api/debug/overview
  Returns recent segments, detections, incidents + embedding coverage stats.
  No auth required in dev (DEV_AUTH_BYPASS=true covers it via middleware).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import Detection, Incident, Segment

router = APIRouter(prefix="/api/debug", tags=["debug"])
logger = logging.getLogger("api.debug")


@router.get("/overview")
def debug_overview(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    # ── Segment stats ──────────────────────────────────────────────────────────
    seg_total      = db.query(func.count(Segment.segment_id)).scalar() or 0
    seg_described  = db.query(func.count(Segment.segment_id)).filter(Segment.description.isnot(None)).scalar() or 0
    seg_embedded   = db.query(func.count(Segment.segment_id)).filter(Segment.description_embedding.isnot(None)).scalar() or 0

    # ── Detection stats ────────────────────────────────────────────────────────
    det_total      = db.query(func.count(Detection.detection_id)).scalar() or 0
    det_vlm        = db.query(func.count(Detection.detection_id)).filter(Detection.vlm_summary.isnot(None)).scalar() or 0
    det_embedded   = db.query(func.count(Detection.detection_id)).filter(Detection.vlm_embedding.isnot(None)).scalar() or 0
    det_high       = db.query(func.count(Detection.detection_id)).filter(Detection.threat_level == "HIGH").scalar() or 0
    det_medium     = db.query(func.count(Detection.detection_id)).filter(Detection.threat_level == "MEDIUM").scalar() or 0

    # ── Incident stats ─────────────────────────────────────────────────────────
    inc_total      = db.query(func.count(Incident.incident_id)).scalar() or 0
    inc_open       = db.query(func.count(Incident.incident_id)).filter(Incident.status == "OPEN").scalar() or 0
    inc_high       = db.query(func.count(Incident.incident_id)).filter(Incident.threat_level == "HIGH").scalar() or 0

    # ── Recent segments ────────────────────────────────────────────────────────
    segments = []
    for seg in db.query(Segment).order_by(Segment.start_time.desc()).limit(limit).all():
        segments.append({
            "segment_id":    str(seg.segment_id)[:8],
            "camera_id":     seg.camera_id,
            "start_time":    seg.start_time.isoformat() if seg.start_time else None,
            "end_time":      seg.end_time.isoformat() if seg.end_time else None,
            "duration_sec":  round(seg.duration_sec, 1) if seg.duration_sec else None,
            "description":   seg.description,
            "has_embedding": seg.description_embedding is not None,
            "thumbnail_url": seg.thumbnail_url,
            "has_motion":    seg.has_motion,
            "has_detections":seg.has_detections,
            "detection_count":seg.detection_count,
            "size_kb":       round(seg.size_bytes / 1024) if seg.size_bytes else None,
        })

    # ── Recent detections ──────────────────────────────────────────────────────
    detections = []
    for det in db.query(Detection).order_by(Detection.detected_at.desc()).limit(limit).all():
        detections.append({
            "detection_id":  str(det.detection_id)[:8],
            "event_id":      det.event_id,
            "camera_id":     det.camera_id,
            "zone_name":     det.zone_name,
            "object_class":  det.object_class,
            "confidence":    round(det.confidence, 3) if det.confidence else None,
            "detected_at":   det.detected_at.isoformat() if det.detected_at else None,
            "dwell_sec":     round(det.dwell_sec, 1) if det.dwell_sec else None,
            "event_type":    det.event_type,
            "threat_level":  det.threat_level,
            "is_threat":     det.is_threat,
            "vlm_summary":   det.vlm_summary,
            "has_embedding": det.vlm_embedding is not None,
            "thumbnail_url": det.thumbnail_url,
            "incident_id":   str(det.incident_id)[:8] if det.incident_id else None,
        })

    # ── Recent incidents ───────────────────────────────────────────────────────
    incidents = []
    for inc in db.query(Incident).order_by(Incident.detected_at.desc()).limit(30).all():
        incidents.append({
            "incident_id":   str(inc.incident_id)[:8],
            "camera_id":     inc.camera_id,
            "zone_name":     inc.zone_name,
            "object_class":  inc.object_class,
            "threat_level":  inc.threat_level,
            "status":        inc.status,
            "summary":       inc.summary,
            "detected_at":   inc.detected_at.isoformat() if inc.detected_at else None,
            "resolved_at":   inc.resolved_at.isoformat() if inc.resolved_at else None,
        })

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "stats": {
            "segments": {
                "total": seg_total,
                "with_description": seg_described,
                "with_embedding": seg_embedded,
                "coverage_pct": round(seg_embedded / seg_total * 100, 1) if seg_total else 0,
            },
            "detections": {
                "total": det_total,
                "with_vlm_summary": det_vlm,
                "with_embedding": det_embedded,
                "coverage_pct": round(det_embedded / det_total * 100, 1) if det_total else 0,
                "high_threat": det_high,
                "medium_threat": det_medium,
            },
            "incidents": {
                "total": inc_total,
                "open": inc_open,
                "high": inc_high,
            },
        },
        "segments":   segments,
        "detections": detections,
        "incidents":  incidents,
    }
