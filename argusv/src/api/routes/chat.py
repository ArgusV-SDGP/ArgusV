"""
api/routes/chat.py — Grounded chat endpoint (RAG + OpenAI)
-----------------------------------------------------------
POST /api/chat
  - Embeds the user message using the local sentence-transformer model
  - Retrieves the most semantically relevant Detection summaries from pgvector
  - Calls the configured LLM with the retrieved context as grounding
  - Returns the assistant answer + source clips for citation

Tasks: CHAT-01
"""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Detection
from embeddings.embeddings import vector_db

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("api.chat")

_SYSTEM_PROMPT = (
    "You are ArgusV, an AI security analyst integrated into a video surveillance system. "
    "You are given real-time camera footage summaries as context. "
    "Use only the provided context to answer questions about detected events, incidents, and observations. "
    "Be concise and factual. If the context does not contain relevant information, say so clearly. "
    "Do not invent events or locations not mentioned in the context."
)


class ChatHistoryItem(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryItem] = []
    camera_id: Optional[str] = None
    zone_id: Optional[str] = None
    limit: int = 6   # number of context clips to retrieve


class SourceClip(BaseModel):
    event_id: str
    camera_id: str
    timestamp: str
    vlm_summary: Optional[str]
    distance: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceClip]


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    """
    Grounded conversational search over camera footage summaries.
    Retrieves semantically relevant clips via pgvector, then asks the LLM
    to answer the user's question using that context.
    """
    if not payload.message.strip():
        raise HTTPException(400, "message must not be empty")

    # ── 1. Embed the user query ──────────────────────────────────────────────
    query_vector = await vector_db.embed_text(payload.message)
    if not query_vector:
        raise HTTPException(500, "Failed to generate embedding for query")

    # ── 2. Retrieve relevant detection summaries ─────────────────────────────
    distance_col = Detection.vlm_embedding.cosine_distance(query_vector).label("distance")
    q = (
        db.query(Detection, distance_col)
        .filter(Detection.vlm_embedding.isnot(None))
        .filter(Detection.vlm_summary.isnot(None))
    )
    if payload.camera_id:
        q = q.filter(Detection.camera_id == payload.camera_id)
    if payload.zone_id:
        q = q.filter(Detection.zone_id == payload.zone_id)

    hits = q.order_by(distance_col).limit(payload.limit).all()

    sources: list[SourceClip] = []
    context_lines: list[str] = []

    for detection, dist in hits:
        sources.append(SourceClip(
            event_id=detection.event_id,
            camera_id=detection.camera_id,
            timestamp=detection.detected_at.isoformat() if detection.detected_at else "",
            vlm_summary=detection.vlm_summary,
            distance=float(dist) if dist is not None else 1.0,
        ))
        ts = detection.detected_at.strftime("%Y-%m-%d %H:%M") if detection.detected_at else "unknown time"
        context_lines.append(
            f"[Camera: {detection.camera_id} | {ts} | Threat: {detection.threat_level or 'N/A'}]\n"
            f"{detection.vlm_summary}"
        )

    context_block = "\n\n".join(context_lines) if context_lines else "No relevant footage found."

    # ── 3. Call the LLM ──────────────────────────────────────────────────────
    if not cfg.OPENAI_API_KEY:
        raise HTTPException(503, "No LLM API key configured (OPENAI_API_KEY)")

    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    # Inject prior conversation turns
    for turn in payload.history[-10:]:   # cap at last 10 turns to stay within context
        if turn.role in {"user", "assistant"}:
            messages.append({"role": turn.role, "content": turn.content})

    # Final user message includes the retrieved context
    grounded_message = (
        f"Relevant footage context:\n{context_block}\n\n"
        f"Question: {payload.message}"
    )
    messages.append({"role": "user", "content": grounded_message})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.VLM_MODEL,
                    "messages": messages,
                    "max_tokens": 512,
                    "temperature": 0.3,
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

    return ChatResponse(answer=answer, sources=sources)
