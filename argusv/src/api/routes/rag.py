"""
api/routes/rag.py — PostgreSQL pgvector RAG Endpoints
-------------------------------------------------------
Handles natural language queries mathematically mapping against 
Postgres embeddings, complete with SQL metadata filtering.
"""

from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from datetime import datetime

from db.connection import get_db
from db.models import Detection
from embeddings.embeddings import vector_db

router = APIRouter(tags=["rag"])

class RAGSearchResult(BaseModel):
    event_id: str
    camera_id: str
    timestamp: str
    vlm_summary: Optional[str]
    distance: float
    thumbnail_url: Optional[str]

@router.get("/api/search", response_model=List[RAGSearchResult], summary="Run Vector Search (pgvector + metadata)")
async def search_semantic_events(
    query: str = Query(..., description="The semantic or descriptive query (e.g. 'person carrying a huge box')"),
    camera_id: str = Query(None, description="Optional: narrow search to a camera"),
    threat_level: str = Query(None, description="Optional: filter by HIGh, MEDIUM, LOW"),
    zone_id: str = Query(None, description="Optional: filter by zone_id"),
    start_time: datetime = Query(None, description="Optional: limit search start date"),
    end_time: datetime = Query(None, description="Optional: limit search end date"),
    limit: int = Query(10, description="Max amount of video clips to return"),
    db: Session = Depends(get_db),
):
    """
    Accepts text, mathematically compares it to the GenAI video 
    summaries using pgvector, applies traditional SQL filtering, 
    and ranks the most relevant video clips.
    """
    
    # 1. Transform the Query into a 384-dimension vector natively
    query_vector = await vector_db.embed_text(query)
    
    if not query_vector:
        raise HTTPException(500, "Failed to natively generate embedding for query.")

    # 2. Build the Hybrid Search Query against Postgres
    # Calculate the mathematical distance and label it
    distance_col = Detection.vlm_embedding.cosine_distance(query_vector).label("distance")
    q = db.query(Detection, distance_col).filter(Detection.vlm_embedding != None)

    # Filtering Metadata (Hard criteria)
    if camera_id:
        q = q.filter(Detection.camera_id == camera_id)
    if threat_level:
        q = q.filter(Detection.threat_level == threat_level.upper())
    if zone_id:
        q = q.filter(Detection.zone_id == zone_id)
    if start_time:
        q = q.filter(Detection.detected_at >= start_time)
    if end_time:
        q = q.filter(Detection.detected_at <= end_time)

    # Mathematical Vector Search: Order by the cosine distance and execute
    q = q.order_by(distance_col).limit(limit)
    hits = q.all()

    # 3. Serialize output
    out = []
    for hit, dist in hits:
        out.append(RAGSearchResult(
            event_id=hit.event_id,
            camera_id=hit.camera_id,
            timestamp=hit.detected_at.isoformat(),
            vlm_summary=hit.vlm_summary,
            distance=float(dist) if dist is not None else 0.0,
            thumbnail_url=hit.thumbnail_url
        ))
        
    return out
