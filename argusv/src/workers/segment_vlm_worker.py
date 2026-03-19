"""
workers/segment_vlm_worker.py — Per-Segment Scene Description + RAG Indexing
-----------------------------------------------------------------------------
Listens on bus.segments for SEGMENT_COMPLETE events.

For each completed video chunk (.ts file):
  1. Extracts a frame from the middle of the segment via FFmpeg
  2. Saves the frame as a JPEG thumbnail (accessible at /recordings/.../thumbnails/)
  3. Calls GPT-4o vision to generate a natural-language scene description
  4. Embeds the description via text-embedding-3-small (1536 dims)
  5. Writes description + description_embedding + thumbnail_url to the DB

This makes every recorded chunk:
  - Searchable via natural language (pgvector RAG)
  - Previewable via thumbnail in the UI
  - Retrievable as a playable video clip via /api/recordings
"""

import asyncio
import base64
import json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx

import config as cfg
from bus import bus

logger = logging.getLogger("segment-vlm-worker")

_PROMPT = (
    "You are reviewing a security camera video clip. "
    "Describe what is happening in this scene in 1-2 clear sentences. "
    "Focus on: who/what is present, their activity, location, and anything noteworthy. "
    "Be concise and factual. Do not speculate."
)


async def segment_vlm_worker() -> None:
    """Main loop — consumes SEGMENT_COMPLETE events from bus.segments."""
    logger.info("[SegmentVLM] Worker started")
    while True:
        try:
            event: dict = await bus.segments.get()
            if event.get("event_type") == "SEGMENT_COMPLETE":
                asyncio.create_task(_process_segment(event))
            bus.segments.task_done()
        except Exception as e:
            logger.error(f"[SegmentVLM] Loop error: {e}", exc_info=True)
            await asyncio.sleep(1)


async def _process_segment(event: dict) -> None:
    """Full pipeline: extract frame → thumbnail → description → embedding → DB."""
    segment_path: Optional[str] = event.get("path")
    camera_id: Optional[str] = event.get("camera_id", "unknown")
    segment_id_str: Optional[str] = event.get("segment_id")

    if not segment_path or not Path(segment_path).exists():
        logger.warning(f"[SegmentVLM] File not found: {segment_path}")
        return

    if not cfg.OPENAI_API_KEY:
        logger.debug("[SegmentVLM] No OPENAI_API_KEY — skipping")
        return

    loop = asyncio.get_running_loop()

    # ── 1. Extract mid-segment frame ──────────────────────────────────────────
    frame_bytes, frame_b64 = await loop.run_in_executor(
        None, _extract_middle_frame, segment_path
    )
    if not frame_b64:
        logger.warning(f"[SegmentVLM] Could not extract frame from {segment_path}")
        return

    # ── 2. Save thumbnail ─────────────────────────────────────────────────────
    thumbnail_url: Optional[str] = None
    if frame_bytes and segment_id_str:
        thumbnail_url = await loop.run_in_executor(
            None, _save_thumbnail, frame_bytes, camera_id, segment_id_str
        )

    # ── 3. Get scene description from GPT-4o ──────────────────────────────────
    description = await _describe_with_gpt4o(frame_b64)
    if not description:
        logger.warning(f"[SegmentVLM] GPT-4o returned no description for {segment_path}")
        return

    logger.info(f"[SegmentVLM] {Path(segment_path).name}: {description}")

    # ── 4. Embed the description ───────────────────────────────────────────────
    from genai.manager import embed_text
    embedding: Optional[list[float]] = await embed_text(description)

    # ── 5. Persist to DB ──────────────────────────────────────────────────────
    await loop.run_in_executor(
        None, _save_to_db,
        segment_path, segment_id_str, description, embedding, thumbnail_url
    )


def _extract_middle_frame(segment_path: str) -> tuple[Optional[bytes], Optional[str]]:
    """
    Extract one JPEG frame from the mid-point of the .ts file.
    Returns (raw_bytes, base64_string) or (None, None) on failure.
    """
    try:
        # Probe duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", segment_path],
            capture_output=True, text=True, timeout=10,
        )
        duration = 0.0
        if probe.returncode == 0:
            info = json.loads(probe.stdout)
            duration = float(info.get("format", {}).get("duration", 0))

        seek = max(0.0, duration / 2)

        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", segment_path,
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg",
             "-vf", "scale=1280:-1", "pipe:1"],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout:
            return None, None

        raw = result.stdout
        return raw, base64.b64encode(raw).decode("utf-8")
    except Exception as e:
        logger.error(f"[SegmentVLM] Frame extraction error: {e}")
        return None, None


def _save_thumbnail(frame_bytes: bytes, camera_id: str, segment_id_str: str) -> Optional[str]:
    """
    Save the JPEG frame to LOCAL_RECORDINGS_DIR/{camera_id}/thumbnails/{segment_id}.jpg
    Returns the web-accessible URL path or None on failure.
    """
    try:
        thumb_dir = Path(cfg.LOCAL_RECORDINGS_DIR) / camera_id / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_file = thumb_dir / f"{segment_id_str}.jpg"
        thumb_file.write_bytes(frame_bytes)
        return f"/recordings/{camera_id}/thumbnails/{segment_id_str}.jpg"
    except Exception as e:
        logger.error(f"[SegmentVLM] Thumbnail save error: {e}")
        return None


async def _describe_with_gpt4o(frame_b64: str) -> Optional[str]:
    """Call GPT-4o vision with the frame and return a scene description."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": cfg.VLM_MODEL,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _PROMPT},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/jpeg;base64,{frame_b64}",
                                "detail": "low",
                            }},
                        ],
                    }],
                    "max_tokens": 150,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[SegmentVLM] GPT-4o error: {e}")
        return None


def _save_to_db(
    segment_path: str,
    segment_id_str: Optional[str],
    description: str,
    embedding: Optional[list[float]],
    thumbnail_url: Optional[str],
) -> None:
    """Write description, embedding, and thumbnail_url to the segments row."""
    from db.connection import get_db_sync
    from db.models import Segment

    db = get_db_sync()
    try:
        seg: Optional[Segment] = None

        if segment_id_str:
            try:
                seg = db.query(Segment).filter(
                    Segment.segment_id == uuid.UUID(segment_id_str)
                ).first()
            except Exception:
                pass

        if not seg:
            seg = db.query(Segment).filter(Segment.minio_path == segment_path).first()

        if not seg:
            logger.warning(f"[SegmentVLM] Segment row not found: {segment_path}")
            return

        seg.description = description
        if embedding:
            seg.description_embedding = embedding
        if thumbnail_url:
            seg.thumbnail_url = thumbnail_url

        db.commit()
        logger.debug(
            f"[SegmentVLM] Saved segment {seg.segment_id} — "
            f"description={'yes'}, embedding={'yes' if embedding else 'no'}, "
            f"thumbnail={'yes' if thumbnail_url else 'no'}"
        )
    except Exception as e:
        logger.error(f"[SegmentVLM] DB write error: {e}")
        db.rollback()
    finally:
        db.close()
