"""
api/routes/debug.py — DB inspection endpoint for debugging
-----------------------------------------------------------
GET  /api/debug/overview          — recent segments, detections, incidents
POST /api/debug/test-notification — inject a fake ALERT to test Telegram/Slack/Webhook
"""

import logging
import time
import uuid
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
        "generated_at":  datetime.utcnow().isoformat(),
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


@router.post("/force-pipeline-alert")
async def force_pipeline_alert(
    threat_level: str = "HIGH",
    object_class: str = "person",
    zone_name: str = "Entrance",
    camera_id: Optional[str] = None,
):
    """
    Inject a fake event into bus.vlm_results so it travels through the FULL
    normal pipeline: decision_engine_worker (DB write) -> notification_worker.
    Unlike /test-notification (which skips to bus.actions), this creates real
    Detection + Incident rows and triggers notifications the same way a live
    camera detection would.

    camera_id: optional — if omitted, the first registered camera in the DB is used.
    """
    from bus import bus
    from db.connection import get_db_session
    from db.models import Camera
    from sqlalchemy import select

    # Resolve a real camera_id to satisfy the FK constraint on detections
    resolved_camera_id = camera_id
    if not resolved_camera_id:
        try:
            async with get_db_session() as db:
                result = await db.execute(select(Camera.camera_id).limit(1))
                resolved_camera_id = result.scalar_one_or_none()
        except Exception:
            pass

    if not resolved_camera_id:
        return {"status": "error", "message": "No camera found in DB. Pass ?camera_id=<id> explicitly."}

    fake_event = {
        "event_id":     str(uuid.uuid4()),
        "event_type":   "START",
        "camera_id":    resolved_camera_id,
        "zone_id":      None,   # None avoids UUID cast error on Incident.zone_id
        "zone_name":    zone_name,
        "object_class": object_class,
        "confidence":   0.95,
        "timestamp":    time.time(),
        "track_id":     1,
        "dwell_sec":    0,
        "bbox":         {"x1": 100, "y1": 100, "x2": 300, "y2": 400},
        "vlm": {
            "threat_level": threat_level.upper(),
            "is_threat":    threat_level.upper() in ("HIGH", "MEDIUM"),
            "summary":      f"[FORCED] {object_class.capitalize()} detected in {zone_name} — {threat_level} threat.",
        },
    }

    await bus.vlm_results.put(fake_event)
    logger.info(f"[Debug] Injected into bus.vlm_results: {threat_level} / {object_class} / {zone_name} / camera={resolved_camera_id}")

    return {
        "status":  "queued",
        "message": "Fake event injected into bus.vlm_results — decision engine will write to DB and fire notifications.",
        "event":   fake_event,
    }


@router.post("/test-notification")
async def test_notification(
    threat_level: str = "HIGH",
    object_class: str = "person",
    zone_name: str = "Test Zone",
    camera_id: str = "test-cam-01",
):
    """
    Inject a fake ALERT action into the pipeline bus to test the full
    notification flow (Telegram / Slack / Webhook) without a real camera.

    Query params:
      threat_level  HIGH | MEDIUM | LOW   (default: HIGH)
      object_class  e.g. person, car      (default: person)
      zone_name     any string            (default: Test Zone)
      camera_id     any string            (default: test-cam-01)
    """
    from bus import bus

    fake_action = {
        "action_type":  "ALERT",
        "event_id":     str(uuid.uuid4()),
        "camera_id":    camera_id,
        "zone_id":      "test-zone",
        "zone_name":    zone_name,
        "object_class": object_class,
        "confidence":   0.92,
        "threat_level": threat_level.upper(),
        "is_threat":    True,
        "summary":      f"[TEST] {object_class.capitalize()} detected in {zone_name} with {threat_level} threat level.",
        "timestamp":    time.time(),
    }

    await bus.actions.put(fake_action)
    logger.info(f"[Debug] Injected test ALERT: {threat_level} / {object_class} / {zone_name}")

    return {
        "status":  "queued",
        "message": "Fake ALERT injected into bus.actions — check Telegram/Slack/logs.",
        "action":  fake_action,
    }
