"""
api/routes/chat.py — Grounded RAG chat over video footage
----------------------------------------------------------
POST /api/chat

Pipeline:
  1. Embed the user query (text-embedding-3-small)
  2. Dual vector search with distance threshold:
       a. Detection.vlm_embedding  — individual threat/detection moments
       b. Segment.description_embedding — full 10-second chunk scene descriptions
  3. Temporal fallback — if query contains time keywords (last hour, today…)
     also pull recent items by timestamp so time-based questions always work
  4. Merge, deduplicate by time window, sort chronologically
  5. Build rich structured context for GPT-4o
  6. Return natural language answer + source clips (thumbnail + video URLs)
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Detection, Segment

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("api.chat")

# ── Constants ─────────────────────────────────────────────────────────────────

# Cosine distance above this = not relevant enough to include
_DISTANCE_THRESHOLD = 0.72

# Time windows for temporal fallback queries
_TEMPORAL_PATTERNS: list[tuple[re.Pattern, timedelta]] = [
    (re.compile(r"\blast\s+hour\b",       re.I), timedelta(hours=1)),
    (re.compile(r"\blast\s+30\s*min",     re.I), timedelta(minutes=30)),
    (re.compile(r"\blast\s+2\s*hours?\b", re.I), timedelta(hours=2)),
    (re.compile(r"\btoday\b",             re.I), timedelta(hours=24)),
    (re.compile(r"\brecent(ly)?\b",       re.I), timedelta(hours=2)),
    (re.compile(r"\bjust now\b",          re.I), timedelta(minutes=15)),
    (re.compile(r"\bthis morning\b",      re.I), timedelta(hours=12)),
    (re.compile(r"\bthis (after|even)",   re.I), timedelta(hours=8)),
]

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are ArgusV, an AI-powered security analyst connected to a live surveillance system.

You are given footage context from real cameras. Each entry is either:
- A DETECTION EVENT: a specific moment when a person/vehicle was detected, classified, and analysed
- A VIDEO CLIP: a 10-second recorded chunk with a scene description

Your response rules:
1. Answer in clear, natural conversational language — not bullet points unless listing many events
2. Always reference specific evidence: mention camera names, timestamps, and what was observed
   Example: "At 14:32 on cam-01, a person was seen loitering near the gate for 45 seconds."
3. If multiple cameras or time windows are relevant, summarise across them clearly
4. For threats: state the threat level (HIGH/MEDIUM/LOW), what was observed, and what action was taken
5. For scene questions ("what was happening"): describe the activity naturally
6. If the context has no relevant footage, say: "No relevant footage was found for that query."
7. Never invent events not in the context
8. If asked about a time range, focus on events within that range
"""

# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatHistoryItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryItem] = []
    camera_id: Optional[str] = None
    zone_id: Optional[str] = None
    limit: int = 10


class SourceClip(BaseModel):
    source_type: Literal["detection", "segment"]
    id: str
    camera_id: str
    timestamp: str
    end_time: Optional[str]
    zone_name: Optional[str]
    description: str
    threat_level: Optional[str]
    is_threat: Optional[bool]
    thumbnail_url: Optional[str]
    video_url: Optional[str]
    playlist_url: Optional[str]
    incident_id: Optional[str]
    score: float   # similarity 0→1 (higher = more relevant)


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceClip]
    total_context_clips: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_time_window(message: str) -> Optional[timedelta]:
    """Return a timedelta if the message contains a temporal keyword."""
    for pattern, delta in _TEMPORAL_PATTERNS:
        if pattern.search(message):
            return delta
    return None


def _playlist_url(camera_id: str, ts: datetime, padding_sec: int = 30) -> str:
    s = (ts - timedelta(seconds=padding_sec)).isoformat()
    e = (ts + timedelta(seconds=padding_sec)).isoformat()
    return f"/api/recordings/{camera_id}/playlist?start={quote(s)}&end={quote(e)}"


def _seg_playlist_url(camera_id: str, start: datetime, end: datetime) -> str:
    return (
        f"/api/recordings/{camera_id}/playlist"
        f"?start={quote(start.isoformat())}&end={quote(end.isoformat())}"
    )


def _ts_label(iso: str) -> str:
    """Format ISO timestamp as human-readable label for context."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso


def _build_context_block(sources: list[SourceClip]) -> str:
    """
    Build a structured, chronologically-sorted context block for the LLM.
    Groups entries by camera so the model can reason per-camera easily.
    """
    if not sources:
        return "No relevant footage found in the database."

    # Sort chronologically
    sorted_sources = sorted(sources, key=lambda s: s.timestamp)

    lines: list[str] = []
    for s in sorted_sources:
        ts = _ts_label(s.timestamp)
        cam = s.camera_id
        zone = f", Zone: {s.zone_name}" if s.zone_name else ""
        threat = f", Threat: {s.threat_level}" if s.threat_level else ""

        if s.source_type == "detection":
            kind = "DETECTION"
            dwell = ""  # could add dwell_sec if we expose it
        else:
            kind = "VIDEO CLIP"
            end = _ts_label(s.end_time) if s.end_time else ""
            dwell = f" → {end}" if end else ""

        relevance = f"(similarity: {s.score:.0%})"
        lines.append(
            f"[{kind} | Camera: {cam} | {ts}{dwell}{zone}{threat}] {relevance}\n"
            f"  {s.description}"
        )

    return "\n\n".join(lines)


def _seg_filename(minio_path: Optional[str]) -> str:
    if not minio_path:
        return ""
    return Path(minio_path).name


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    if not payload.message.strip():
        raise HTTPException(400, "message must not be empty")

    if not cfg.OPENAI_API_KEY:
        raise HTTPException(503, "OPENAI_API_KEY not configured")

    # ── 1. Embed query ────────────────────────────────────────────────────────
    from genai.manager import embed_text
    query_vector = await embed_text(payload.message)
    if not query_vector:
        raise HTTPException(503, "Embedding service unavailable — check OPENAI_API_KEY")

    half = max(2, payload.limit // 2)
    time_window = _detect_time_window(payload.message)
    now = datetime.utcnow()

    sources: list[SourceClip] = []

    # ── 2a. Detection vector search ───────────────────────────────────────────
    try:
        dist_col = Detection.vlm_embedding.cosine_distance(query_vector).label("distance")
        q = (
            db.query(Detection, dist_col)
            .filter(Detection.vlm_embedding.isnot(None))
            .filter(Detection.vlm_summary.isnot(None))
            .filter(dist_col <= _DISTANCE_THRESHOLD)
        )
        if payload.camera_id:
            q = q.filter(Detection.camera_id == payload.camera_id)
        if payload.zone_id:
            q = q.filter(Detection.zone_id == payload.zone_id)
        if time_window:
            q = q.filter(Detection.detected_at >= now - time_window)

        for det, dist in q.order_by(dist_col).limit(half).all():
            sources.append(SourceClip(
                source_type="detection",
                id=str(det.detection_id),
                camera_id=det.camera_id,
                timestamp=det.detected_at.isoformat() if det.detected_at else "",
                end_time=None,
                zone_name=det.zone_name,
                description=det.vlm_summary or "",
                threat_level=det.threat_level,
                is_threat=det.is_threat,
                thumbnail_url=det.thumbnail_url,
                video_url=None,
                playlist_url=_playlist_url(det.camera_id, det.detected_at) if det.detected_at else None,
                incident_id=str(det.incident_id) if det.incident_id else None,
                score=round(max(0.0, 1.0 - float(dist)), 4),
            ))
    except Exception as e:
        logger.warning(f"[Chat] Detection vector search failed: {e}")

    # ── 2b. Segment vector search ─────────────────────────────────────────────
    try:
        seg_dist = Segment.description_embedding.cosine_distance(query_vector).label("distance")
        sq = (
            db.query(Segment, seg_dist)
            .filter(Segment.description_embedding.isnot(None))
            .filter(Segment.description.isnot(None))
            .filter(seg_dist <= _DISTANCE_THRESHOLD)
        )
        if payload.camera_id:
            sq = sq.filter(Segment.camera_id == payload.camera_id)
        if time_window:
            sq = sq.filter(Segment.start_time >= now - time_window)

        for seg, dist in sq.order_by(seg_dist).limit(half).all():
            fname = _seg_filename(seg.minio_path)
            sources.append(SourceClip(
                source_type="segment",
                id=str(seg.segment_id),
                camera_id=seg.camera_id,
                timestamp=seg.start_time.isoformat() if seg.start_time else "",
                end_time=seg.end_time.isoformat() if seg.end_time else None,
                zone_name=None,
                description=seg.description or "",
                threat_level=None,
                is_threat=None,
                thumbnail_url=seg.thumbnail_url,
                video_url=f"/recordings/{seg.camera_id}/{fname}" if fname else None,
                playlist_url=_seg_playlist_url(seg.camera_id, seg.start_time, seg.end_time)
                             if seg.start_time and seg.end_time else None,
                incident_id=None,
                score=round(max(0.0, 1.0 - float(dist)), 4),
            ))
    except Exception as e:
        logger.warning(f"[Chat] Segment vector search failed: {e}")

    # ── 3. Temporal fallback — if time-based query has no vector hits ─────────
    if time_window and len(sources) < 3:
        try:
            cutoff = now - time_window
            recent_dets = (
                db.query(Detection)
                .filter(Detection.detected_at >= cutoff)
                .filter(Detection.vlm_summary.isnot(None))
            )
            if payload.camera_id:
                recent_dets = recent_dets.filter(Detection.camera_id == payload.camera_id)
            for det in recent_dets.order_by(Detection.detected_at.desc()).limit(4).all():
                det_id = str(det.detection_id)
                if any(s.id == det_id for s in sources):
                    continue
                sources.append(SourceClip(
                    source_type="detection",
                    id=det_id,
                    camera_id=det.camera_id,
                    timestamp=det.detected_at.isoformat() if det.detected_at else "",
                    end_time=None,
                    zone_name=det.zone_name,
                    description=det.vlm_summary or "",
                    threat_level=det.threat_level,
                    is_threat=det.is_threat,
                    thumbnail_url=det.thumbnail_url,
                    video_url=None,
                    playlist_url=_playlist_url(det.camera_id, det.detected_at) if det.detected_at else None,
                    incident_id=str(det.incident_id) if det.incident_id else None,
                    score=0.5,  # time-based fallback, no vector score
                ))
        except Exception as e:
            logger.warning(f"[Chat] Temporal fallback failed: {e}")

    # ── 4. Deduplicate overlapping detection + segment from same window ───────
    # Keep the higher-score entry when detection and segment overlap within 15s
    deduped: list[SourceClip] = []
    for src in sorted(sources, key=lambda s: s.score, reverse=True):
        overlap = False
        for kept in deduped:
            if kept.camera_id != src.camera_id:
                continue
            try:
                t1 = datetime.fromisoformat(src.timestamp)
                t2 = datetime.fromisoformat(kept.timestamp)
                if abs((t1 - t2).total_seconds()) < 15:
                    overlap = True
                    break
            except Exception:
                pass
        if not overlap:
            deduped.append(src)

    # Sort final list chronologically for clean context
    final = sorted(deduped[:payload.limit], key=lambda s: s.timestamp)

    # ── 5. Build LLM context block ────────────────────────────────────────────
    context_block = _build_context_block(final)

    # ── 6. Call GPT-4o ────────────────────────────────────────────────────────
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    for turn in payload.history[-10:]:
        if turn.role in {"user", "assistant"}:
            messages.append({"role": turn.role, "content": turn.content})

    messages.append({"role": "user", "content": (
        f"--- Footage Context ---\n{context_block}\n\n"
        f"--- Question ---\n{payload.message}"
    )})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": cfg.VLM_MODEL,
                    "messages": messages,
                    "max_tokens": 600,
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as e:
        logger.error(f"[Chat] OpenAI error: {e.response.text}")
        raise HTTPException(502, f"LLM request failed: {e.response.status_code}")
    except Exception as e:
        logger.error(f"[Chat] Unexpected error: {e}", exc_info=True)
        raise HTTPException(500, "Chat request failed")

    return ChatResponse(
        answer=answer,
        sources=final,
        total_context_clips=len(final),
    )
