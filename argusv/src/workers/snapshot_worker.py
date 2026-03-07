"""
workers/snapshot_worker.py — Thumbnail / Snapshot capture
----------------------------------------------------------
Tasks: REC-14, REC-15

Consumes bus.raw_detections for START events.
Crops the bounding box region, saves JPEG thumbnail.
Updates Detection.thumbnail_url in DB.
"""

import asyncio
import logging
import base64
import cv2
import numpy as np
from pathlib import Path

import config as cfg
from bus import bus

logger = logging.getLogger("snapshot-worker")

SNAPSHOT_DIR = Path("/recordings/snapshots")


async def snapshot_worker():
    """
    Listens on a snapshot queue (tapped from raw_detections).
    For every START event with an embedded frame → save thumbnail.
    Task REC-14
    """
    logger.info("[Snapshot] Worker started")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        # TODO REC-14: tap into raw_detections without consuming
        # Currently we'd need a second queue or a fanout mechanism.
        # Option: add bus.snapshots queue in bus.py and have
        # stream_ingestion_worker also put on bus.snapshots for START events.
        await asyncio.sleep(1)  # placeholder


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
        x1   = max(0, bbox.get("x1", 0) - 20)   # 20px padding
        y1   = max(0, bbox.get("y1", 0) - 20)
        x2   = min(w, bbox.get("x2", w) + 20)
        y2   = min(h, bbox.get("y2", h) + 20)

        crop = frame[y1:y2, x1:x2]

        # Save thumbnail
        cam_id   = event.get("camera_id", "cam")
        event_id = event.get("event_id", "evt")[:8]
        fname    = f"{cam_id}_{event_id}.jpg"
        out_path = SNAPSHOT_DIR / cam_id / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        logger.info(f"[Snapshot] Saved: {out_path}")
        return str(out_path)

    except Exception as e:
        logger.error(f"[Snapshot] Failed: {e}")
        return None
