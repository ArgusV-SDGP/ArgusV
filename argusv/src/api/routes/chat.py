"""
api/routes/chat.py — Agentic video-chat over surveillance footage
-----------------------------------------------------------------
POST /api/chat

Agent loop:
  1. Load session history from Redis (session_id → last 20 turns)
  2. Call GPT-4o with tool definitions (up to MAX_TOOL_ROUNDS rounds)
  3. Execute tool calls: search_detections, search_segments, get_incidents,
     get_zone_activity, get_clip
  4. Synthesize final answer from accumulated evidence
  5. Save updated session back to Redis (TTL 2h)

Returns: answer, sources, session_id, tool steps taken
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Detection, Incident, Segment

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("api.chat")

# ── Constants ─────────────────────────────────────────────────────────────────

_DISTANCE_THRESHOLD = 0.72
MAX_TOOL_ROUNDS = 3
SESSION_TTL = 7200  # 2 hours
SESSION_KEY = "argus:chat:session:{}"

_TEMPORAL_PATTERNS: list[tuple[re.Pattern, timedelta]] = [
    (re.compile(r"\blast\s+hour\b",       re.I), timedelta(hours=1)),
    (re.compile(r"\blast\s+30\s*min",     re.I), timedelta(minutes=30)),
    (re.compile(r"\blast\s+2\s*hours?\b", re.I), timedelta(hours=2)),
    (re.compile(r"\btoday\b",             re.I), timedelta(hours=24)),
    (re.compile(r"\brecent(ly)?\b",       re.I), timedelta(hours=2)),
    (re.compile(r"\bjust now\b",          re.I), timedelta(minutes=15)),
    (re.compile(r"\bthis morning\b",      re.I), timedelta(hours=12)),
]

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are ArgusV, an AI security analyst with live access to a surveillance system.

You have tools to query detection events, video segments, incidents, and zone activity.
Always use tools to gather evidence before answering — never make up events.

Rules:
- Use search_detections for specific threat/person/vehicle queries
- Use search_segments for broader scene-level questions
- Use get_incidents when asked about incidents or security events
- Use get_zone_activity for zone-specific activity summaries
- Use get_clip to retrieve a video URL for a specific moment
- After gathering evidence, synthesize a clear, natural answer
- Always cite camera names, timestamps, and exact observations
- If no evidence is found, say so honestly
"""

# ── OpenAI tool definitions ───────────────────────────────────────────────────

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_detections",
            "description": (
                "Search detection events using semantic similarity. Use for specific threat queries, "
                "person/vehicle lookups, or behaviour searches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":            {"type": "string",  "description": "Natural language search query"},
                    "camera_id":        {"type": "string",  "description": "Restrict to this camera (optional)"},
                    "zone_id":          {"type": "string",  "description": "Restrict to this zone ID (optional)"},
                    "time_range_hours": {"type": "number",  "description": "Look back N hours (default 24)"},
                    "threat_level":     {"type": "string",  "enum": ["HIGH", "MEDIUM", "LOW"], "description": "Filter by threat level (optional)"},
                    "limit":            {"type": "integer", "description": "Max results (default 6)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_segments",
            "description": (
                "Search recorded video segment descriptions. Use for scene-level queries about what "
                "was happening in an area over a time window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":            {"type": "string",  "description": "Natural language search query"},
                    "camera_id":        {"type": "string",  "description": "Restrict to this camera (optional)"},
                    "time_range_hours": {"type": "number",  "description": "Look back N hours (default 24)"},
                    "limit":            {"type": "integer", "description": "Max results (default 4)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_incidents",
            "description": "Retrieve recent security incidents from the database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_id":        {"type": "string"},
                    "severity":         {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                    "time_range_hours": {"type": "number", "description": "Look back N hours (default 24)"},
                    "limit":            {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_zone_activity",
            "description": "Summarise detection activity by zone. Use for zone-based questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone_id":          {"type": "string"},
                    "camera_id":        {"type": "string"},
                    "time_range_hours": {"type": "number", "description": "Look back N hours (default 24)"},
                    "limit":            {"type": "integer", "description": "Max detections to fetch (default 20)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clip",
            "description": "Get a video clip playlist URL for a specific moment on a camera.",
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_id":   {"type": "string", "description": "Camera ID"},
                    "timestamp":   {"type": "string", "description": "ISO 8601 timestamp"},
                    "padding_sec": {"type": "integer", "description": "Seconds of padding each side (default 30)"},
                },
                "required": ["camera_id", "timestamp"],
            },
        },
    },
]

# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    camera_id: Optional[str] = None  # global camera filter
    limit: int = 8


class SourceClip(BaseModel):
    source_type: Literal["detection", "segment", "incident"]
    id: str
    camera_id: str
    timestamp: str
    end_time: Optional[str] = None
    zone_name: Optional[str] = None
    description: str
    threat_level: Optional[str] = None
    is_threat: Optional[bool] = None
    thumbnail_url: Optional[str] = None
    video_url: Optional[str] = None
    playlist_url: Optional[str] = None
    incident_id: Optional[str] = None
    score: float = 0.0


class AgentStep(BaseModel):
    tool: str
    args: dict
    result_summary: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[SourceClip]
    total_context_clips: int
    steps: list[AgentStep]


# ── Session memory (Redis) ────────────────────────────────────────────────────

async def _load_session(session_id: str) -> list[dict]:
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
        try:
            raw = await r.get(SESSION_KEY.format(session_id))
            if raw:
                return json.loads(raw)
        finally:
            await r.aclose()
    except Exception as e:
        logger.debug(f"[Chat] Session load failed: {e}")
    return []


async def _save_session(session_id: str, messages: list[dict]) -> None:
    # Keep last 20 turns (user+assistant pairs = 40 messages), trim older
    trimmed = messages[-40:]
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
        try:
            await r.setex(SESSION_KEY.format(session_id), SESSION_TTL, json.dumps(trimmed))
        finally:
            await r.aclose()
    except Exception as e:
        logger.debug(f"[Chat] Session save failed: {e}")


# ── URL helpers ───────────────────────────────────────────────────────────────

def _playlist_url(camera_id: str, ts: datetime, padding_sec: int = 30) -> str:
    s = (ts - timedelta(seconds=padding_sec)).isoformat()
    e = (ts + timedelta(seconds=padding_sec)).isoformat()
    return f"/api/recordings/{camera_id}/playlist?start={quote(s)}&end={quote(e)}"


def _seg_playlist_url(camera_id: str, start: datetime, end: datetime) -> str:
    return (
        f"/api/recordings/{camera_id}/playlist"
        f"?start={quote(start.isoformat())}&end={quote(end.isoformat())}"
    )


def _seg_filename(minio_path: Optional[str]) -> str:
    return Path(minio_path).name if minio_path else ""


# ── Tool execution ────────────────────────────────────────────────────────────

async def _exec_search_detections(
    args: dict,
    db: Session,
    cam_filter: Optional[str],
) -> tuple[dict, list[SourceClip]]:
    from genai.manager import embed_text

    query = args.get("query", "")
    limit = min(int(args.get("limit", 6)), 12)
    hours = float(args.get("time_range_hours", 24))
    threat_filter = args.get("threat_level")
    zone_filter = args.get("zone_id")
    cam = args.get("camera_id") or cam_filter
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours)

    sources: list[SourceClip] = []
    items: list[dict] = []

    vec = await embed_text(query)
    if vec:
        try:
            dist_col = Detection.vlm_embedding.cosine_distance(vec).label("distance")
            q = (
                db.query(Detection, dist_col)
                .filter(Detection.vlm_embedding.isnot(None))
                .filter(Detection.vlm_summary.isnot(None))
                .filter(dist_col <= _DISTANCE_THRESHOLD)
                .filter(Detection.detected_at >= cutoff)
            )
            if cam:
                q = q.filter(Detection.camera_id == cam)
            if zone_filter:
                q = q.filter(Detection.zone_id == zone_filter)
            if threat_filter:
                q = q.filter(Detection.threat_level == threat_filter)

            for det, dist in q.order_by(dist_col).limit(limit).all():
                score = round(max(0.0, 1.0 - float(dist)), 4)
                purl = _playlist_url(det.camera_id, det.detected_at) if det.detected_at else None
                src = SourceClip(
                    source_type="detection",
                    id=str(det.detection_id),
                    camera_id=det.camera_id,
                    timestamp=det.detected_at.isoformat() if det.detected_at else "",
                    zone_name=det.zone_name,
                    description=det.vlm_summary or "",
                    threat_level=det.threat_level,
                    is_threat=det.is_threat,
                    thumbnail_url=det.thumbnail_url,
                    playlist_url=purl,
                    incident_id=str(det.incident_id) if det.incident_id else None,
                    score=score,
                )
                sources.append(src)
                items.append({
                    "camera": det.camera_id,
                    "zone": det.zone_name,
                    "timestamp": det.detected_at.isoformat() if det.detected_at else "",
                    "threat": det.threat_level,
                    "summary": det.vlm_summary or "",
                    "score": score,
                })
        except Exception as e:
            logger.warning(f"[Chat] search_detections vector error: {e}")

    # Temporal fallback if vector returned nothing
    if not items:
        q2 = (
            db.query(Detection)
            .filter(Detection.vlm_summary.isnot(None))
            .filter(Detection.detected_at >= cutoff)
        )
        if cam:
            q2 = q2.filter(Detection.camera_id == cam)
        if threat_filter:
            q2 = q2.filter(Detection.threat_level == threat_filter)
        for det in q2.order_by(Detection.detected_at.desc()).limit(limit).all():
            purl = _playlist_url(det.camera_id, det.detected_at) if det.detected_at else None
            src = SourceClip(
                source_type="detection",
                id=str(det.detection_id),
                camera_id=det.camera_id,
                timestamp=det.detected_at.isoformat() if det.detected_at else "",
                zone_name=det.zone_name,
                description=det.vlm_summary or "",
                threat_level=det.threat_level,
                is_threat=det.is_threat,
                thumbnail_url=det.thumbnail_url,
                playlist_url=purl,
                incident_id=str(det.incident_id) if det.incident_id else None,
                score=0.5,
            )
            sources.append(src)
            items.append({
                "camera": det.camera_id,
                "zone": det.zone_name,
                "timestamp": det.detected_at.isoformat() if det.detected_at else "",
                "threat": det.threat_level,
                "summary": det.vlm_summary or "",
                "score": 0.5,
            })

    result = {"count": len(items), "detections": items}
    return result, sources


async def _exec_search_segments(
    args: dict,
    db: Session,
    cam_filter: Optional[str],
) -> tuple[dict, list[SourceClip]]:
    from genai.manager import embed_text

    query = args.get("query", "")
    limit = min(int(args.get("limit", 4)), 8)
    hours = float(args.get("time_range_hours", 24))
    cam = args.get("camera_id") or cam_filter
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours)

    sources: list[SourceClip] = []
    items: list[dict] = []

    vec = await embed_text(query)
    if vec:
        try:
            dist_col = Segment.description_embedding.cosine_distance(vec).label("distance")
            q = (
                db.query(Segment, dist_col)
                .filter(Segment.description_embedding.isnot(None))
                .filter(Segment.description.isnot(None))
                .filter(dist_col <= _DISTANCE_THRESHOLD)
                .filter(Segment.start_time >= cutoff)
            )
            if cam:
                q = q.filter(Segment.camera_id == cam)

            for seg, dist in q.order_by(dist_col).limit(limit).all():
                score = round(max(0.0, 1.0 - float(dist)), 4)
                fname = _seg_filename(seg.minio_path)
                purl = _seg_playlist_url(seg.camera_id, seg.start_time, seg.end_time) \
                    if seg.start_time and seg.end_time else None
                src = SourceClip(
                    source_type="segment",
                    id=str(seg.segment_id),
                    camera_id=seg.camera_id,
                    timestamp=seg.start_time.isoformat() if seg.start_time else "",
                    end_time=seg.end_time.isoformat() if seg.end_time else None,
                    description=seg.description or "",
                    thumbnail_url=seg.thumbnail_url,
                    video_url=f"/recordings/{seg.camera_id}/{fname}" if fname else None,
                    playlist_url=purl,
                    score=score,
                )
                sources.append(src)
                items.append({
                    "camera": seg.camera_id,
                    "start": seg.start_time.isoformat() if seg.start_time else "",
                    "end": seg.end_time.isoformat() if seg.end_time else "",
                    "description": seg.description or "",
                    "score": score,
                })
        except Exception as e:
            logger.warning(f"[Chat] search_segments vector error: {e}")

    result = {"count": len(items), "segments": items}
    return result, sources


def _exec_get_incidents(
    args: dict,
    db: Session,
    cam_filter: Optional[str],
) -> tuple[dict, list[SourceClip]]:
    limit = min(int(args.get("limit", 8)), 20)
    hours = float(args.get("time_range_hours", 24))
    cam = args.get("camera_id") or cam_filter
    severity = args.get("severity")
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours)

    q = db.query(Incident).filter(Incident.detected_at >= cutoff)
    if cam:
        q = q.filter(Incident.camera_id == cam)
    if severity:
        q = q.filter(Incident.threat_level == severity)

    items: list[dict] = []
    sources: list[SourceClip] = []

    for inc in q.order_by(Incident.detected_at.desc()).limit(limit).all():
        items.append({
            "id": str(inc.incident_id),
            "camera": inc.camera_id,
            "zone": inc.zone_name,
            "object_class": inc.object_class,
            "threat": inc.threat_level,
            "status": inc.status,
            "summary": inc.summary,
            "timestamp": inc.detected_at.isoformat() if inc.detected_at else "",
        })
        sources.append(SourceClip(
            source_type="incident",
            id=str(inc.incident_id),
            camera_id=inc.camera_id or "",
            timestamp=inc.detected_at.isoformat() if inc.detected_at else "",
            zone_name=inc.zone_name,
            description=inc.summary or "",
            threat_level=inc.threat_level,
            incident_id=str(inc.incident_id),
            score=0.8,
        ))

    result = {"count": len(items), "incidents": items}
    return result, sources


def _exec_get_zone_activity(
    args: dict,
    db: Session,
    cam_filter: Optional[str],
) -> tuple[dict, list[SourceClip]]:
    limit = min(int(args.get("limit", 20)), 50)
    hours = float(args.get("time_range_hours", 24))
    zone_filter = args.get("zone_id")
    cam = args.get("camera_id") or cam_filter
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=hours)

    q = (
        db.query(Detection)
        .filter(Detection.detected_at >= cutoff)
        .filter(Detection.zone_name.isnot(None))
    )
    if cam:
        q = q.filter(Detection.camera_id == cam)
    if zone_filter:
        q = q.filter(Detection.zone_id == zone_filter)

    # Group by zone
    zone_stats: dict[str, dict[str, Any]] = {}
    sources: list[SourceClip] = []

    for det in q.order_by(Detection.detected_at.desc()).limit(limit).all():
        zn = det.zone_name or "unknown"
        if zn not in zone_stats:
            zone_stats[zn] = {"camera": det.camera_id, "count": 0, "threats": 0, "last_seen": "", "samples": []}
        zone_stats[zn]["count"] += 1
        if det.is_threat:
            zone_stats[zn]["threats"] += 1
        if not zone_stats[zn]["last_seen"]:
            zone_stats[zn]["last_seen"] = det.detected_at.isoformat() if det.detected_at else ""
        if len(zone_stats[zn]["samples"]) < 2 and det.vlm_summary:
            zone_stats[zn]["samples"].append(det.vlm_summary)
            purl = _playlist_url(det.camera_id, det.detected_at) if det.detected_at else None
            sources.append(SourceClip(
                source_type="detection",
                id=str(det.detection_id),
                camera_id=det.camera_id,
                timestamp=det.detected_at.isoformat() if det.detected_at else "",
                zone_name=zn,
                description=det.vlm_summary or "",
                threat_level=det.threat_level,
                is_threat=det.is_threat,
                thumbnail_url=det.thumbnail_url,
                playlist_url=purl,
                score=0.6,
            ))

    zones_list = [{"zone": k, **v} for k, v in zone_stats.items()]
    result = {"count": len(zones_list), "zones": zones_list}
    return result, sources


def _exec_get_clip(args: dict) -> tuple[dict, list[SourceClip]]:
    cam = args.get("camera_id", "")
    ts_str = args.get("timestamp", "")
    padding = int(args.get("padding_sec", 30))

    try:
        ts = datetime.fromisoformat(ts_str)
        purl = _playlist_url(cam, ts, padding)
        result = {"camera_id": cam, "timestamp": ts_str, "playlist_url": purl}
        src = SourceClip(
            source_type="detection",
            id=f"clip-{cam}-{ts_str}",
            camera_id=cam,
            timestamp=ts_str,
            description=f"Clip around {ts_str} on {cam}",
            playlist_url=purl,
            score=1.0,
        )
        return result, [src]
    except Exception as e:
        return {"error": str(e)}, []


async def _execute_tool(
    name: str,
    args: dict,
    db: Session,
    cam_filter: Optional[str],
) -> tuple[dict, list[SourceClip]]:
    try:
        if name == "search_detections":
            return await _exec_search_detections(args, db, cam_filter)
        elif name == "search_segments":
            return await _exec_search_segments(args, db, cam_filter)
        elif name == "get_incidents":
            return _exec_get_incidents(args, db, cam_filter)
        elif name == "get_zone_activity":
            return _exec_get_zone_activity(args, db, cam_filter)
        elif name == "get_clip":
            return _exec_get_clip(args)
        else:
            return {"error": f"Unknown tool: {name}"}, []
    except Exception as e:
        logger.error(f"[Chat] Tool '{name}' error: {e}", exc_info=True)
        return {"error": str(e)}, []


# ── Dedup helper ──────────────────────────────────────────────────────────────

def _dedup_sources(sources: list[SourceClip]) -> list[SourceClip]:
    """Remove duplicate sources (same id) and near-duplicate by camera+time window."""
    seen_ids: set[str] = set()
    deduped: list[SourceClip] = []
    for src in sorted(sources, key=lambda s: s.score, reverse=True):
        if src.id in seen_ids:
            continue
        seen_ids.add(src.id)
        overlap = False
        for kept in deduped:
            if kept.camera_id != src.camera_id or kept.source_type != src.source_type:
                continue
            try:
                t1 = datetime.fromisoformat(src.timestamp)
                t2 = datetime.fromisoformat(kept.timestamp)
                if abs((t1 - t2).total_seconds()) < 15:
                    overlap = True
                    break
            except Exception as e:
                logger.warning(f"[Chat] pgvector search unavailable: {e}")
        if not overlap:
            deduped.append(src)
    return deduped


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

    # ── Session ───────────────────────────────────────────────────────────────
    session_id = payload.session_id or str(uuid.uuid4())
    session_history = await _load_session(session_id)

    # ── Build initial message list ────────────────────────────────────────────
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.extend(session_history[-40:])  # last 20 pairs
    messages.append({"role": "user", "content": payload.message})

    headers = {
        "Authorization": f"Bearer {cfg.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    all_sources: list[SourceClip] = []
    steps: list[AgentStep] = []

    # ── Agent loop ────────────────────────────────────────────────────────────
    answer = "I was unable to retrieve any information. Please try again."

    async with httpx.AsyncClient(timeout=45) as client:
        for round_num in range(MAX_TOOL_ROUNDS + 1):
            use_tools = round_num < MAX_TOOL_ROUNDS

            body: dict = {
                "model": cfg.VLM_MODEL,
                "messages": messages,
                "max_tokens": 800,
                "temperature": 0.2,
            }
            if use_tools:
                body["tools"] = _TOOLS
                body["tool_choice"] = "auto"

            try:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=body,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(f"[Chat] OpenAI error: {e.response.text}")
                raise HTTPException(502, f"LLM request failed: {e.response.status_code}")
            except Exception as e:
                logger.error(f"[Chat] Unexpected error: {e}", exc_info=True)
                raise HTTPException(500, "Chat request failed")

            choice = resp.json()["choices"][0]
            msg = choice["message"]
            finish_reason = choice.get("finish_reason", "stop")

            # No tool calls → final answer
            if not msg.get("tool_calls") or finish_reason == "stop":
                answer = (msg.get("content") or "").strip()
                # Append assistant turn for session history
                messages.append({"role": "assistant", "content": answer})
                break

            # Execute tool calls
            messages.append(msg)  # assistant message with tool_calls

            for tc in msg["tool_calls"]:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    fn_args = {}

                tool_result, clips = await _execute_tool(fn_name, fn_args, db, payload.camera_id)
                all_sources.extend(clips)

                # Build step summary
                count = tool_result.get("count", len(clips))
                step_summary = f"Found {count} result(s)"
                if "error" in tool_result:
                    step_summary = f"Error: {tool_result['error']}"
                steps.append(AgentStep(tool=fn_name, args=fn_args, result_summary=step_summary))

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_result),
                })

    # ── Deduplicate and limit sources ─────────────────────────────────────────
    final_sources = sorted(
        _dedup_sources(all_sources)[:payload.limit],
        key=lambda s: s.timestamp,
    )

    # ── Save session (user + assistant turns only, no tool noise) ─────────────
    new_history = session_history + [
        {"role": "user", "content": payload.message},
        {"role": "assistant", "content": answer},
    ]
    await _save_session(session_id, new_history)

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        sources=final_sources,
        total_context_clips=len(final_sources),
        steps=steps,
    )
