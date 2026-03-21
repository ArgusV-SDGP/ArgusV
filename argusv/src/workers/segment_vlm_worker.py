"""
workers/segment_vlm_worker.py — Per-Segment Scene Description + RAG Indexing
-----------------------------------------------------------------------------
Listens on bus.segments for SEGMENT_COMPLETE events.

Description pipeline (in priority order):
  1. Gemini (SEGMENT_VLM_PROVIDER=gemini) — uploads full video, native understanding
  2. OpenAI multi-frame — extracts FRAMES_PER_SEGMENT frames, sends all to GPT-4o
  3. Detection synthesis — fallback when VLM fails: combines vlm_summary values
     already stored on Detection rows in the segment's time window (no API call)

All paths produce a description that gets embedded and stored, so RAG always works
even if ffmpeg or the VLM API is unavailable.
"""

import asyncio
import base64
import json
import logging
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

import config as cfg
from bus import bus

logger = logging.getLogger("segment-vlm-worker")

FRAMES_PER_SEGMENT = 4

_GEMINI_BASE        = "https://generativelanguage.googleapis.com"
_GEMINI_UPLOAD_BASE = f"{_GEMINI_BASE}/upload/v1beta"
_GEMINI_API_BASE    = f"{_GEMINI_BASE}/v1beta"


# ── Prompts ───────────────────────────────────────────────────────────────────

def _build_prompt(duration_sec: float, source: str) -> str:
    if source == "gemini":
        return (
            f"You are reviewing a {duration_sec:.0f}-second security camera clip. "
            "Describe what happens in this clip in 2-3 sentences: "
            "who/what is present, what activity or movement occurs over time, "
            "and anything noteworthy for security purposes. "
            "Be factual and concise. Do not speculate beyond what is visible."
        )
    return (
        f"You are reviewing a {duration_sec:.0f}-second security camera clip. "
        f"The following frames are sampled at equal intervals in chronological order "
        "(earliest → latest). Describe what happens in 2-3 sentences: "
        "who/what is present, what activity or movement occurs, anything noteworthy. "
        "Be factual and concise."
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

async def segment_vlm_worker() -> None:
    provider = cfg.SEGMENT_VLM_PROVIDER
    logger.info(f"[SegmentVLM] Worker started — provider: {provider}")

    # Backfill any existing segments that have no description yet
    asyncio.create_task(_backfill_existing_segments())

    while True:
        try:
            event: dict = await bus.segments.get()
            if event.get("event_type") == "SEGMENT_COMPLETE":
                asyncio.create_task(_process_segment(event))
            bus.segments.task_done()
        except Exception as e:
            logger.error(f"[SegmentVLM] Loop error: {e}", exc_info=True)
            await asyncio.sleep(1)


async def _backfill_existing_segments() -> None:
    """
    On startup: find all Segment rows with description=NULL and process them.
    Runs after a short delay to let the DB settle.
    Caps at 200 most-recent to avoid a thundering herd on first boot.
    """
    await asyncio.sleep(5)  # let DB + other workers start up first

    from db.connection import get_db_sync
    from db.models import Segment as _Seg

    loop = asyncio.get_running_loop()
    pending_events: list[dict] = await loop.run_in_executor(None, _query_pending_segments)

    if not pending_events:
        logger.info("[SegmentVLM] Backfill: all segments already have descriptions")
        return

    logger.info(f"[SegmentVLM] Backfill: queuing {len(pending_events)} segments for description")
    for event in pending_events:
        asyncio.create_task(_process_segment(event))
        await asyncio.sleep(0.1)   # slight spacing to avoid hammering the API


def _query_pending_segments() -> list[dict]:
    from db.connection import get_db_sync
    from db.models import Segment as _Seg

    db = get_db_sync()
    try:
        rows = (
            db.query(_Seg)
            .filter(_Seg.description.is_(None))
            .order_by(_Seg.start_time.desc())
            .limit(200)
            .all()
        )
        return [
            {
                "event_type": "SEGMENT_COMPLETE",
                "camera_id":  r.camera_id,
                "path":       r.minio_path,
                "segment_id": str(r.segment_id),
                "start_time": r.start_time.isoformat() if r.start_time else None,
                "end_time":   r.end_time.isoformat()   if r.end_time   else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[SegmentVLM] Backfill query error: {e}")
        return []
    finally:
        db.close()


# ── Per-segment pipeline ──────────────────────────────────────────────────────

async def _process_segment(event: dict) -> None:
    segment_path: Optional[str] = event.get("path")
    camera_id: Optional[str]    = event.get("camera_id", "unknown")
    segment_id_str               = event.get("segment_id")
    start_time_str               = event.get("start_time")
    end_time_str                 = event.get("end_time")
    name                         = Path(segment_path).name if segment_path else "?"

    file_exists = bool(segment_path and Path(segment_path).exists())
    if not file_exists:
        # File may have been cleaned up — skip VLM tiers but still allow
        # detection synthesis so the segment gets indexed for RAG.
        logger.debug(f"[SegmentVLM] {name}: file not on disk, will use detection synthesis")

    logger.debug(f"[SegmentVLM] Processing {name} (cam={camera_id}, file={'yes' if file_exists else 'no'})")
    loop = asyncio.get_running_loop()

    # ── Extract frames (for thumbnail + OpenAI path) — only if file exists ────
    frames: list[tuple[bytes, str]] = []
    duration_sec: float = float(getattr(cfg, "SEGMENT_DURATION_SEC", 10))

    if file_exists:
        frames, duration_sec = await loop.run_in_executor(
            None, _extract_frames, segment_path, FRAMES_PER_SEGMENT
        )
        if frames:
            logger.debug(f"[SegmentVLM] {name}: extracted {len(frames)} frames, {duration_sec:.1f}s")
        else:
            logger.warning(f"[SegmentVLM] {name}: ffmpeg frame extraction failed — ffmpeg on PATH?")

    # ── Thumbnail (middle frame) ───────────────────────────────────────────────
    thumbnail_url: Optional[str] = None
    if frames and segment_id_str:
        mid_bytes = frames[len(frames) // 2][0]
        thumbnail_url = await loop.run_in_executor(
            None, _save_thumbnail, mid_bytes, camera_id, segment_id_str
        )

    # ── Description: try each path in order ───────────────────────────────────
    description: Optional[str] = None
    provider = cfg.SEGMENT_VLM_PROVIDER

    # Path 1 — Gemini full-video (requires file on disk)
    if file_exists and provider == "gemini":
        if not cfg.GEMINI_API_KEY:
            logger.warning(f"[SegmentVLM] SEGMENT_VLM_PROVIDER=gemini but GEMINI_API_KEY not set — skipping")
        else:
            logger.debug(f"[SegmentVLM] {name}: calling Gemini ({cfg.GEMINI_VISION_MODEL})")
            description = await _describe_with_gemini(segment_path, duration_sec)
            if description:
                logger.info(f"[SegmentVLM] [gemini] {name}: {description[:100]}")
            else:
                logger.warning(f"[SegmentVLM] {name}: Gemini failed, trying OpenAI frames")

    # Path 2 — OpenAI multi-frame (requires file + frames on disk)
    if description is None and file_exists:
        if not cfg.OPENAI_API_KEY:
            logger.warning(f"[SegmentVLM] {name}: OPENAI_API_KEY not set, skipping OpenAI path")
        elif not frames:
            logger.warning(f"[SegmentVLM] {name}: no frames extracted for OpenAI path")
        else:
            logger.debug(f"[SegmentVLM] {name}: calling OpenAI with {len(frames)} frames")
            description = await _describe_with_openai(
                [b64 for _, b64 in frames], duration_sec
            )
            if description:
                logger.info(f"[SegmentVLM] [openai] {name}: {description[:100]}")
            else:
                logger.warning(f"[SegmentVLM] {name}: OpenAI frames also failed")

    # Path 3 — Detection synthesis fallback (no API call, no ffmpeg needed)
    if description is None:
        logger.warning(f"[SegmentVLM] {name}: all VLM paths failed — synthesizing from detections")
        description = await loop.run_in_executor(
            None, _synthesize_from_detections,
            camera_id, start_time_str, end_time_str, segment_id_str
        )
        if description:
            logger.info(f"[SegmentVLM] [synth] {name}: {description[:100]}")
        else:
            logger.error(
                f"[SegmentVLM] {name}: FAILED all paths. "
                f"Check: OPENAI_API_KEY set? ffmpeg on PATH? Gemini key valid? "
                f"Detections linked to segment?"
            )
            return

    # ── Embed + persist ───────────────────────────────────────────────────────
    from genai.manager import embed_text
    embedding: Optional[list[float]] = await embed_text(description)
    if not embedding:
        logger.warning(f"[SegmentVLM] {name}: embedding failed — OPENAI_API_KEY needed for embed_text")

    await loop.run_in_executor(
        None, _save_to_db,
        segment_path, segment_id_str, description, embedding, thumbnail_url
    )


# ── Gemini full-video path ────────────────────────────────────────────────────

async def _describe_with_gemini(segment_path: str, duration_sec: float) -> Optional[str]:
    loop = asyncio.get_running_loop()
    tmp_mp4: Optional[Path] = None
    file_name: Optional[str] = None

    try:
        tmp_mp4 = await loop.run_in_executor(None, _remux_to_mp4, segment_path)
        if not tmp_mp4:
            logger.warning(f"[SegmentVLM] Gemini: mp4 remux failed (ffmpeg missing?)")
            return None

        video_bytes = tmp_mp4.read_bytes()
        if not video_bytes:
            logger.warning(f"[SegmentVLM] Gemini: remuxed mp4 is empty")
            return None

        logger.debug(f"[SegmentVLM] Gemini: uploading {len(video_bytes)//1024}KB mp4")

        async with httpx.AsyncClient(timeout=90) as client:
            # Upload
            upload_resp = await client.post(
                f"{_GEMINI_UPLOAD_BASE}/files",
                params={"key": cfg.GEMINI_API_KEY, "uploadType": "media"},
                headers={"Content-Type": "video/mp4"},
                content=video_bytes,
            )
            if not upload_resp.is_success:
                logger.warning(f"[SegmentVLM] Gemini upload failed: {upload_resp.status_code} {upload_resp.text[:200]}")
                return None

            file_info = upload_resp.json().get("file", {})
            file_name = file_info.get("name")   # e.g. "files/abc123"

            if not file_name:
                logger.warning(f"[SegmentVLM] Gemini: upload response missing file name: {upload_resp.text[:200]}")
                return None

            logger.debug(f"[SegmentVLM] Gemini: uploaded as {file_name}, polling for ACTIVE")

            # Poll until ACTIVE — uri is only available in the poll response
            file_uri: Optional[str] = None
            for attempt in range(20):
                await asyncio.sleep(2)
                state_resp = await client.get(
                    f"{_GEMINI_API_BASE}/{file_name}",
                    params={"key": cfg.GEMINI_API_KEY},
                )
                if not state_resp.is_success:
                    logger.warning(f"[SegmentVLM] Gemini: state poll failed: {state_resp.status_code}")
                    break
                poll_data = state_resp.json()
                state = poll_data.get("state", "PROCESSING")
                if state == "ACTIVE":
                    file_uri = poll_data.get("uri")
                    break
                if state == "FAILED":
                    logger.warning(f"[SegmentVLM] Gemini: file processing FAILED")
                    return None
                logger.debug(f"[SegmentVLM] Gemini: state={state} (attempt {attempt+1})")
            else:
                logger.warning(f"[SegmentVLM] Gemini: file never became ACTIVE after 20 attempts")
                return None

            if not file_uri:
                logger.warning(f"[SegmentVLM] Gemini: ACTIVE but no uri in response")
                return None

            # Generate
            prompt = _build_prompt(duration_sec, "gemini")
            gen_resp = await client.post(
                f"{_GEMINI_API_BASE}/models/{cfg.GEMINI_VISION_MODEL}:generateContent",
                params={"key": cfg.GEMINI_API_KEY},
                json={"contents": [{"parts": [
                    {"text": prompt},
                    {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}},
                ]}]},
                timeout=45,
            )
            if not gen_resp.is_success:
                logger.warning(f"[SegmentVLM] Gemini generateContent failed: {gen_resp.status_code} {gen_resp.text[:200]}")
                return None

            description = (
                gen_resp.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )

            # Cleanup
            try:
                await client.delete(
                    f"{_GEMINI_API_BASE}/{file_name}",
                    params={"key": cfg.GEMINI_API_KEY},
                )
                file_name = None
            except Exception:
                pass

        return description or None

    except Exception as e:
        logger.error(f"[SegmentVLM] Gemini unexpected error: {e}", exc_info=True)
        return None
    finally:
        if tmp_mp4 and tmp_mp4.exists():
            try:
                tmp_mp4.unlink()
            except Exception:
                pass


def _remux_to_mp4(segment_path: str) -> Optional[Path]:
    """ffmpeg remux .ts → .mp4 (copy streams, no re-encode, ~instant)."""
    try:
        tmp = Path(tempfile.mktemp(suffix=".mp4"))
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", segment_path,
             "-c", "copy", "-movflags", "+faststart", str(tmp)],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            logger.warning(f"[SegmentVLM] ffmpeg remux error: {result.stderr[-300:].decode(errors='replace')}")
            if tmp.exists():
                tmp.unlink()
            return None
        return tmp
    except FileNotFoundError:
        logger.error("[SegmentVLM] ffmpeg not found on PATH — install ffmpeg")
        return None
    except Exception as e:
        logger.error(f"[SegmentVLM] remux error: {e}")
        return None


# ── OpenAI multi-frame path ───────────────────────────────────────────────────

async def _describe_with_openai(frames_b64: list[str], duration_sec: float) -> Optional[str]:
    prompt = _build_prompt(duration_sec, "openai")
    content: list[dict] = [{"type": "text", "text": prompt}]
    for b64 in frames_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": cfg.VLM_MODEL,
                      "messages": [{"role": "user", "content": content}],
                      "max_tokens": 200},
            )
            if not resp.is_success:
                logger.warning(f"[SegmentVLM] OpenAI error: {resp.status_code} {resp.text[:200]}")
                return None
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"[SegmentVLM] OpenAI multi-frame error: {e}")
        return None


# ── Detection synthesis fallback ──────────────────────────────────────────────

def _synthesize_from_detections(
    camera_id: Optional[str],
    start_time_str: Optional[str],
    end_time_str: Optional[str],
    segment_id_str: Optional[str],
) -> Optional[str]:
    """
    Build a segment description from Detection.vlm_summary values in the
    segment's time window. No API call, no ffmpeg needed.
    Falls back further to a simple class/zone summary if no VLM summaries exist.
    """
    from db.connection import get_db_sync
    from db.models import Detection, Segment

    db = get_db_sync()
    try:
        # Resolve time bounds from DB segment or event fields
        start_dt: Optional[datetime] = None
        end_dt:   Optional[datetime] = None

        if segment_id_str:
            try:
                seg = db.query(Segment).filter(
                    Segment.segment_id == uuid.UUID(segment_id_str)
                ).first()
                if seg:
                    start_dt, end_dt = seg.start_time, seg.end_time
            except Exception:
                pass

        if not start_dt and start_time_str:
            try:
                start_dt = datetime.fromisoformat(start_time_str)
            except Exception:
                pass
        if not end_dt and end_time_str:
            try:
                end_dt = datetime.fromisoformat(end_time_str)
            except Exception:
                pass

        if not start_dt or not end_dt or not camera_id:
            logger.warning("[SegmentVLM] Synth: cannot determine time bounds")
            return None

        # Try real VLM summaries — exclude the "no VLM" placeholder
        _PLACEHOLDER = "Motion detected (No VLM analysis)"
        dets_with_vlm = (
            db.query(Detection)
            .filter(
                Detection.camera_id    == camera_id,
                Detection.vlm_summary  .isnot(None),
                Detection.vlm_summary  != _PLACEHOLDER,
                Detection.detected_at  >= start_dt,
                Detection.detected_at  <= end_dt,
            )
            .order_by(Detection.detected_at)
            .limit(5)
            .all()
        )

        if dets_with_vlm:
            # Deduplicate similar summaries (first 50 chars as key)
            seen: set[str] = set()
            unique: list[str] = []
            for d in dets_with_vlm:
                key = (d.vlm_summary or "")[:50]
                if key not in seen:
                    seen.add(key)
                    unique.append(d.vlm_summary)
            description = " ".join(unique[:3])
            logger.debug(f"[SegmentVLM] Synth: built from {len(unique)} VLM summaries")
            return description

        # No VLM summaries — build from raw detection classes/zones
        all_dets = (
            db.query(Detection)
            .filter(
                Detection.camera_id  == camera_id,
                Detection.detected_at >= start_dt,
                Detection.detected_at <= end_dt,
            )
            .order_by(Detection.detected_at)
            .limit(20)
            .all()
        )

        if not all_dets:
            return f"Security camera clip on {camera_id}. No detections recorded in this segment."

        classes = list(dict.fromkeys(d.object_class for d in all_dets if d.object_class))
        zones   = list(dict.fromkeys(d.zone_name for d in all_dets if d.zone_name))
        threats = [d for d in all_dets if d.is_threat]

        parts = [f"{', '.join(classes)} detected on camera {camera_id}"]
        if zones:
            parts.append(f"in zone(s): {', '.join(zones)}")
        if threats:
            parts.append(f"{len(threats)} threat event(s) flagged")

        description = ". ".join(parts) + "."
        logger.debug(f"[SegmentVLM] Synth: built from {len(all_dets)} raw detections")
        return description

    except Exception as e:
        logger.error(f"[SegmentVLM] Synth error: {e}", exc_info=True)
        return None
    finally:
        db.close()


# ── Frame extraction ──────────────────────────────────────────────────────────

def _extract_frames(segment_path: str, n: int) -> tuple[list[tuple[bytes, str]], float]:
    duration_sec = 0.0
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", segment_path],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            duration_sec = float(
                json.loads(probe.stdout).get("format", {}).get("duration", 0)
            )
    except FileNotFoundError:
        logger.error("[SegmentVLM] ffprobe not found on PATH — install ffmpeg")
        return [], float(cfg.SEGMENT_DURATION_SEC)
    except Exception as e:
        logger.warning(f"[SegmentVLM] ffprobe error: {e}")

    if duration_sec <= 0:
        duration_sec = float(cfg.SEGMENT_DURATION_SEC)

    margin = max(0.2, duration_sec * 0.05)
    usable = duration_sec - 2 * margin
    if n <= 1 or usable <= 0:
        seek_times = [duration_sec / 2]
    else:
        step = usable / (n - 1)
        seek_times = [margin + i * step for i in range(n)]

    frames: list[tuple[bytes, str]] = []
    for seek in seek_times:
        tmp_jpg = Path(tempfile.mktemp(suffix=".jpg"))
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", segment_path,
                 "-ss", f"{seek:.3f}",
                 "-frames:v", "1", "-vf", "scale=854:-1", str(tmp_jpg)],
                capture_output=True, timeout=15,
            )
            if result.returncode == 0 and tmp_jpg.exists() and tmp_jpg.stat().st_size > 0:
                raw = tmp_jpg.read_bytes()
                frames.append((raw, base64.b64encode(raw).decode("utf-8")))
            else:
                logger.debug(f"[SegmentVLM] Frame at {seek:.1f}s failed rc={result.returncode}")
        except Exception as e:
            logger.warning(f"[SegmentVLM] Frame at {seek:.1f}s: {e}")
        finally:
            if tmp_jpg.exists():
                try:
                    tmp_jpg.unlink()
                except Exception:
                    pass

    return frames, duration_sec


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_thumbnail(frame_bytes: bytes, camera_id: str, segment_id_str: str) -> Optional[str]:
    try:
        thumb_dir = Path(cfg.LOCAL_RECORDINGS_DIR) / camera_id / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        (thumb_dir / f"{segment_id_str}.jpg").write_bytes(frame_bytes)
        return f"/recordings/{camera_id}/thumbnails/{segment_id_str}.jpg"
    except Exception as e:
        logger.error(f"[SegmentVLM] Thumbnail error: {e}")
        return None


def _save_to_db(
    segment_path: str,
    segment_id_str: Optional[str],
    description: str,
    embedding: Optional[list[float]],
    thumbnail_url: Optional[str],
) -> None:
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
        logger.info(
            f"[SegmentVLM] ✅ Saved {str(seg.segment_id)[:8]} — "
            f"desc={len(description)}ch, embedded={'yes' if embedding else 'no'}, "
            f"thumb={'yes' if thumbnail_url else 'no'}"
        )
    except Exception as e:
        logger.error(f"[SegmentVLM] DB write error: {e}")
        db.rollback()
    finally:
        db.close()
