"""
api/routes/rag.py — Unified Semantic Search (Detections + Segments)
--------------------------------------------------------------------
GET /api/search?q=person+near+entrance

Searches BOTH tables via pgvector cosine similarity:
  - Detection.vlm_embedding  (individual threat/detection moments)
  - Segment.description_embedding (10-second video chunk descriptions)

Results are merged, ranked by similarity score, and returned with
thumbnail URLs and playable video/playlist URLs.

Optional filters: camera_id, threat_level, zone_id, start_time, end_time,
                  source_type (detection | segment | all)
"""

from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Detection, Segment

router = APIRouter(tags=["search"])

_DISTANCE_THRESHOLD = 0.75   # drop results with cosine distance above this


# ── Schemas ───────────────────────────────────────────────────────────────────

class SearchResult(BaseModel):
    source_type: Literal["detection", "segment"]
    id: str
    camera_id: str
    timestamp: str
    end_time: Optional[str]
    zone_name: Optional[str]
    object_class: Optional[str]
    description: str               # vlm_summary (detection) or description (segment)
    threat_level: Optional[str]
    is_threat: Optional[bool]
    score: float                   # similarity 0→1 (higher = more relevant)
    thumbnail_url: Optional[str]
    video_url: Optional[str]       # direct .ts URL for segments
    playlist_url: Optional[str]    # HLS ±30s playlist (detections) or full chunk (segments)
    incident_id: Optional[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _det_playlist(camera_id: str, ts: datetime, padding: int = 30) -> str:
    s = (ts - __import__("datetime").timedelta(seconds=padding)).isoformat()
    e = (ts + __import__("datetime").timedelta(seconds=padding)).isoformat()
    return f"/api/recordings/{camera_id}/playlist?start={quote(s)}&end={quote(e)}"


def _seg_playlist(camera_id: str, start: datetime, end: datetime) -> str:
    return (
        f"/api/recordings/{camera_id}/playlist"
        f"?start={quote(start.isoformat())}&end={quote(end.isoformat())}"
    )


def _seg_video_url(camera_id: str, minio_path: Optional[str]) -> Optional[str]:
    if not minio_path:
        return None
    return f"/recordings/{camera_id}/{Path(minio_path).name}"


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get(
    "/api/search",
    response_model=List[SearchResult],
    summary="Semantic search across detections and video clips",
)
async def semantic_search(
    q: str = Query(..., description="Natural language query, e.g. 'person loitering near entrance'"),
    camera_id: Optional[str] = Query(None),
    threat_level: Optional[str] = Query(None, description="HIGH | MEDIUM | LOW"),
    zone_id: Optional[str] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    source_type: Literal["detection", "segment", "all"] = Query("all"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    from genai.manager import embed_text

    query_vector = await embed_text(q)
    if not query_vector:
        raise HTTPException(503, "Embedding service unavailable — check OPENAI_API_KEY")

    results: list[SearchResult] = []
    half = max(2, limit // 2)

    # ── Detection search ──────────────────────────────────────────────────────
    if source_type in ("detection", "all"):
        try:
            dist_col = Detection.vlm_embedding.cosine_distance(query_vector).label("distance")
            dq = (
                db.query(Detection, dist_col)
                .filter(Detection.vlm_embedding.isnot(None))
                .filter(Detection.vlm_summary.isnot(None))
                .filter(dist_col <= _DISTANCE_THRESHOLD)
            )
            if camera_id:
                dq = dq.filter(Detection.camera_id == camera_id)
            if threat_level:
                dq = dq.filter(Detection.threat_level == threat_level.upper())
            if zone_id:
                dq = dq.filter(Detection.zone_id == zone_id)
            if start_time:
                dq = dq.filter(Detection.detected_at >= start_time)
            if end_time:
                dq = dq.filter(Detection.detected_at <= end_time)

            fetch = half if source_type == "all" else limit
            for det, dist in dq.order_by(dist_col).limit(fetch).all():
                results.append(SearchResult(
                    source_type="detection",
                    id=str(det.detection_id),
                    camera_id=det.camera_id,
                    timestamp=det.detected_at.isoformat() if det.detected_at else "",
                    end_time=None,
                    zone_name=det.zone_name,
                    object_class=det.object_class,
                    description=det.vlm_summary or "",
                    threat_level=det.threat_level,
                    is_threat=det.is_threat,
                    score=round(max(0.0, 1.0 - float(dist)), 4),
                    thumbnail_url=det.thumbnail_url,
                    video_url=None,
                    playlist_url=_det_playlist(det.camera_id, det.detected_at) if det.detected_at else None,
                    incident_id=str(det.incident_id) if det.incident_id else None,
                ))
        except Exception as e:
            pass  # pgvector extension or column may not be ready yet

    # ── Segment search ────────────────────────────────────────────────────────
    if source_type in ("segment", "all"):
        try:
            seg_dist = Segment.description_embedding.cosine_distance(query_vector).label("distance")
            sq = (
                db.query(Segment, seg_dist)
                .filter(Segment.description_embedding.isnot(None))
                .filter(Segment.description.isnot(None))
                .filter(seg_dist <= _DISTANCE_THRESHOLD)
            )
            if camera_id:
                sq = sq.filter(Segment.camera_id == camera_id)
            if start_time:
                sq = sq.filter(Segment.start_time >= start_time)
            if end_time:
                sq = sq.filter(Segment.end_time <= end_time)

            fetch = half if source_type == "all" else limit
            for seg, dist in sq.order_by(seg_dist).limit(fetch).all():
                results.append(SearchResult(
                    source_type="segment",
                    id=str(seg.segment_id),
                    camera_id=seg.camera_id,
                    timestamp=seg.start_time.isoformat() if seg.start_time else "",
                    end_time=seg.end_time.isoformat() if seg.end_time else None,
                    zone_name=None,
                    object_class=None,
                    description=seg.description or "",
                    threat_level=None,
                    is_threat=None,
                    score=round(max(0.0, 1.0 - float(dist)), 4),
                    thumbnail_url=seg.thumbnail_url,
                    video_url=_seg_video_url(seg.camera_id, seg.minio_path),
                    playlist_url=_seg_playlist(seg.camera_id, seg.start_time, seg.end_time)
                                 if seg.start_time and seg.end_time else None,
                    incident_id=None,
                ))
        except Exception as e:
            pass

    if not results:
        return []

    # Merge and re-rank by score descending, cap at limit
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
