"""
workers/snapshot_worker.py — Thumbnail / Snapshot & MP4 Clip capture
----------------------------------------------------------------------
Tasks: REC-14, REC-15

Consumes bus.snapshots for START events to generate thumbnails.
Consumes bus.clips for GENERATE_CLIP events to generate MP4 segments.
"""

import asyncio
import logging
import base64
import cv2
import numpy as np
from pathlib import Path
import os
import subprocess

import config as cfg
from bus import bus
from db.connection import get_db_session
from db.models import Segment

logger = logging.getLogger("snapshot-worker")

LOCAL_RECORDINGS_DIR = Path(os.getenv("LOCAL_RECORDINGS_DIR", "/recordings"))
SNAPSHOT_DIR = LOCAL_RECORDINGS_DIR / "snapshots"
CLIPS_DIR = LOCAL_RECORDINGS_DIR / "clips"

async def snapshot_worker():
    """
    Listens on a snapshot queue (tapped from raw_detections).
    For every START event with an embedded frame → save thumbnail.
    Task REC-14
    """
    logger.info("📸 [Snapshot] Worker started")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            event = await bus.snapshots.get()
            # Run in thread so it doesn't block the async loop
            await asyncio.to_thread(save_snapshot, event)
            bus.snapshots.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Snapshot] Loop error: {e}")
            await asyncio.sleep(5)

def save_snapshot(event: dict) -> str | None:
    """
    Synchronous helper: decode frame, crop bbox, save JPEG.
    Returns local path or None on failure.
    Task REC-14
    """
    frame_b64 = event.get("trigger_frame_b64")
    if not frame_b64:
        return None

    try:
        # Decode frame
        img_bytes = base64.b64decode(frame_b64)
        arr       = np.frombuffer(img_bytes, np.uint8)
        frame     = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None

        h, w = frame.shape[:2]
        bbox = event.get("bbox", {})
        x1   = max(0, int(bbox.get("x1", 0)) - 20)   # 20px padding
        y1   = max(0, int(bbox.get("y1", 0)) - 20)
        x2   = min(w, int(bbox.get("x2", w)) + 20)
        y2   = min(h, int(bbox.get("y2", h)) + 20)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
             crop = frame

        cam_id   = event.get("camera_id", "cam")
        event_id = event.get("event_id", "evt")
        fname    = f"{cam_id}_{event_id}.jpg"
        out_path = SNAPSHOT_DIR / cam_id / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        logger.info(f"📸 [Snapshot] Saved Thumbnail: {out_path}")
        return str(out_path)

    except Exception as e:
        logger.error(f"[Snapshot] Failed: {e}", exc_info=True)
        return None


async def clip_generation_worker():
    """
    Task REC-15: Listen for GENERATE_CLIP events and stitch segments.
    """
    logger.info("🎬 [ClipGenerator] Worker started")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    
    while True:
        try:
            action = await bus.clips.get()
            if action.get("action_type") == "GENERATE_CLIP":
                event_id = action.get("event_id")
                camera_id = action.get("camera_id")

                if event_id and camera_id:
                     await asyncio.to_thread(_generate_clip, event_id, camera_id)

            bus.clips.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[ClipGenerator] Loop error: {e}")
            await asyncio.sleep(5)


def _generate_clip(event_id: str, camera_id: str):
    """
    Queries actual Database for segments tied to this detection,
    then uses ffmpeg to concatenate them.
    """
    from db.connection import get_db_sync
    from db.models import Detection, Segment
    from sqlalchemy import select

    out_file = CLIPS_DIR / camera_id / f"{event_id}.mp4"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    
    db = get_db_sync()
    try:
        # 1. Get the segment linked to the detection (or multiple if we locked multiple)
        # Often a detection spans multiple segments. In maintainer, we locked all overlapping
        # segments but only assigned ONE segment id to the detection row. 
        # But wait, maintainer locks all segments. Let's find segments by event window instead
        det = db.query(Detection).filter(Detection.event_id == event_id).first()
        if not det:
            return
            
        incident = det.incident
        if not incident:
            return

        # Fetch all overlapping segments for the incident timeframe!
        segments = db.query(Segment).filter(
            Segment.camera_id == camera_id,
            Segment.end_time >= incident.detected_at
        ).order_by(Segment.start_time).limit(5).all() # Max 50 secs (10s * 5 segments)
        
        if not segments:
            logger.warning(f"🎬 [ClipGenerator] No segments found for {event_id}")
            return
            
        concat_file = CLIPS_DIR / camera_id / f"{event_id}_list.txt"
        with open(concat_file, "w") as f:
            for s in segments:
                if os.path.exists(s.minio_path):
                    f.write(f"file '{s.minio_path}'\n")

        # Use FFmpeg to concat without re-encoding
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(out_file)
        ]
        
        logger.debug(f"🎬 [ClipGenerator] Executing: {' '.join(cmd)}")
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            logger.info(f"🎬 [ClipGenerator] MP4 Clip created: {out_file}")
        else:
            logger.error(f"🎬 [ClipGenerator] FFmpeg Error: {proc.stderr.decode('utf-8')}")

        if os.path.exists(concat_file):
            os.remove(concat_file)

    except Exception as e:
         logger.error(f"[ClipGenerator] Critical error creating clip {event_id}: {e}", exc_info=True)
    finally:
        db.close()
