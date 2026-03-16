"""
api/routes/recordings.py — NVR Replay API
Tasks: API-12, API-13, API-14, API-15
"""

from datetime import datetime, timedelta
import math
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Detection, Incident, Segment

router = APIRouter(tags=["recordings"])


def _serialize_segment(seg: Segment) -> dict[str, Any]:
    # Ensure minio_path (local path) is served as a web-accessible URL
    # /recordings/cam-01/cam-01_123.ts
    path = seg.minio_path
    if "/recordings/" in path:
        web_path = "/recordings/" + path.split("/recordings/")[-1].replace("\\", "/")
    else:
        web_path = path

    return {
        "segment_id": str(seg.segment_id),
        "camera_id": seg.camera_id,
        "start_time": seg.start_time.isoformat(),
        "end_time": seg.end_time.isoformat(),
        "duration_sec": seg.duration_sec,
        "url": web_path,
        "size_bytes": seg.size_bytes,
        "has_motion": seg.has_motion,
        "has_detections": seg.has_detections,
        "detection_count": seg.detection_count,
        "locked": seg.locked,
    }


@router.get("/api/recordings/{camera_id}")
def list_segments(
    camera_id: str,
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    only_events: bool = Query(False),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    q = db.query(Segment).filter(Segment.camera_id == camera_id)
    if start:
        q = q.filter(Segment.end_time >= start)
    if end:
        q = q.filter(Segment.start_time <= end)
    if only_events:
        q = q.filter(Segment.has_detections == True)
    rows = q.order_by(Segment.start_time.asc()).all()
    return [_serialize_segment(s) for s in rows]


@router.get("/api/recordings/{camera_id}/playlist")
def hls_playlist(
    camera_id: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    segs = (
        db.query(Segment)
        .filter(
            Segment.camera_id == camera_id,
            Segment.end_time >= start,
            Segment.start_time <= end,
        )
        .order_by(Segment.start_time.asc())
        .all()
    )
    if not segs:
        raise HTTPException(404, "No segments found for requested range")

    target_dur = max(1, math.ceil(max(float(s.duration_sec or 1) for s in segs)))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_dur}",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for seg in segs:
        lines.append(f"#EXTINF:{float(seg.duration_sec or 0):.3f},")
        # Use web-accessible URL for the playlist
        path = seg.minio_path
        web_path = "/recordings/" + path.split("/recordings/")[-1].replace("\\", "/")
        lines.append(web_path)
    lines.append("#EXT-X-ENDLIST")
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        media_type="application/vnd.apple.mpegurl",
    )


@router.get("/api/recordings/{camera_id}/timeline")
def detection_timeline(
    camera_id: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    threats_only: bool = Query(False),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    q = db.query(Detection).filter(
        Detection.camera_id == camera_id,
        Detection.detected_at >= start,
        Detection.detected_at <= end,
    )
    if threats_only:
        q = q.filter(Detection.is_threat == True)
    dets = q.order_by(Detection.detected_at.asc()).all()
    return {
        "camera_id": camera_id,
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "markers": [
            {
                "detection_id": str(d.detection_id),
                "incident_id": str(d.incident_id) if d.incident_id else None,
                "event_id": d.event_id,
                "timestamp": d.detected_at.isoformat(),
                "object_class": d.object_class,
                "threat_level": d.threat_level,
                "is_threat": d.is_threat,
                "zone_name": d.zone_name,
                "bbox": {
                    "x1": d.bbox_x1,
                    "y1": d.bbox_y1,
                    "x2": d.bbox_x2,
                    "y2": d.bbox_y2,
                },
                "thumbnail_url": d.thumbnail_url,
            }
            for d in dets
        ],
    }


@router.get("/api/incidents/{incident_id}/replay")
def incident_replay(
    incident_id: str,
    padding_sec: int = Query(15),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    try:
        iid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Invalid incident_id")

    inc = db.query(Incident).filter(Incident.incident_id == iid).first()
    if not inc:
        raise HTTPException(404, "Incident not found")
    if not inc.camera_id:
        raise HTTPException(409, "Incident has no camera_id for replay")
    if not inc.detected_at:
        raise HTTPException(409, "Incident missing detected_at timestamp")

    start = inc.detected_at - timedelta(seconds=padding_sec)
    end = inc.detected_at + timedelta(seconds=padding_sec)

    segs = (
        db.query(Segment)
        .filter(
            Segment.camera_id == inc.camera_id,
            Segment.end_time >= start,
            Segment.start_time <= end,
        )
        .order_by(Segment.start_time.asc())
        .all()
    )
    return {
        "incident_id": incident_id,
        "camera_id": inc.camera_id,
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "padding_sec": padding_sec,
        },
        "playlist_url": f"/api/recordings/{inc.camera_id}/playlist?start={start.isoformat()}&end={end.isoformat()}",
        "timeline_url": f"/api/recordings/{inc.camera_id}/timeline?start={start.isoformat()}&end={end.isoformat()}&threats_only=true",
        "segments": [_serialize_segment(s) for s in segs],
    }
