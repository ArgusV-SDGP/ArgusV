"""
api/routes/rag.py — Semantic Search via pgvector
--------------------------------------------------
GET /api/search?q=person+in+red+jacket

Embeds the query text using text-embedding-3-small (same model used when
detections are written), then runs cosine similarity against
Detection.vlm_embedding in PostgreSQL.

Optional filters: camera_id, threat_level, zone_id, start_time, end_time.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Detection, Incident

router = APIRouter(tags=["search"])


class SearchResult(BaseModel):
    detection_id: str
    event_id: Optional[str]
    camera_id: str
    zone_name: Optional[str]
    object_class: Optional[str]
    threat_level: Optional[str]
    is_threat: Optional[bool]
    vlm_summary: Optional[str]
    detected_at: str
    score: float          # 1 - cosine_distance (higher = more similar)
    incident_id: Optional[str]
    thumbnail_url: Optional[str]


@router.get(
    "/api/search",
    response_model=List[SearchResult],
    summary="Semantic search across VLM descriptions",
)
async def semantic_search(
    q: str = Query(..., description="Natural language query, e.g. 'person in red jacket near entrance'"),
    camera_id: Optional[str] = Query(None),
    threat_level: Optional[str] = Query(None, description="HIGH | MEDIUM | LOW"),
    zone_id: Optional[str] = Query(None),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    from genai.manager import embed_text

    # 1. Embed the query text
    query_vector = await embed_text(q)
    if not query_vector:
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable — check OPENAI_API_KEY",
        )

    # 2. Cosine distance search via pgvector
    # cosine_distance returns 0 (identical) → 2 (opposite); convert to score 0→1
    distance_col = Detection.vlm_embedding.cosine_distance(query_vector).label("distance")

    base_q = (
        db.query(Detection, distance_col)
        .filter(Detection.vlm_embedding.isnot(None))
    )

    if camera_id:
        base_q = base_q.filter(Detection.camera_id == camera_id)
    if threat_level:
        base_q = base_q.filter(Detection.threat_level == threat_level.upper())
    if zone_id:
        base_q = base_q.filter(Detection.zone_id == zone_id)
    if start_time:
        base_q = base_q.filter(Detection.detected_at >= start_time)
    if end_time:
        base_q = base_q.filter(Detection.detected_at <= end_time)

    hits = base_q.order_by(distance_col).limit(limit).all()

    # 3. Serialize
    return [
        SearchResult(
            detection_id  = str(det.detection_id),
            event_id      = det.event_id,
            camera_id     = det.camera_id,
            zone_name     = det.zone_name,
            object_class  = det.object_class,
            threat_level  = det.threat_level,
            is_threat     = det.is_threat,
            vlm_summary   = det.vlm_summary,
            detected_at   = det.detected_at.isoformat(),
            score         = round(max(0.0, 1.0 - float(dist)), 4),
            incident_id   = str(det.incident_id) if det.incident_id else None,
            thumbnail_url = det.thumbnail_url,
        )
        for det, dist in hits
    ]
