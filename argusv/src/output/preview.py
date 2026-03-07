"""
output/preview.py — Low-res preview frame generator
-----------------------------------------------------
Frigate equivalent: frigate/output/preview.py

Generates low-resolution preview JPEGs for the
recordings timeline (thumbnail scrubbing).
One preview frame stored per segment (~10s).

TODO DNVR-05: wire into recording_worker on segment complete
"""

import cv2
import numpy as np
import logging
from pathlib import Path

logger = logging.getLogger("output.preview")

PREVIEW_DIR  = "/recordings/previews"
PREVIEW_SIZE = (320, 240)   # width × height


def generate_preview(frame: np.ndarray, camera_id: str, epoch: int) -> str | None:
    """
    Downscale frame and save as preview JPEG.
    Returns saved path or None on failure.
    Task: DNVR-05
    """
    try:
        small = cv2.resize(frame, PREVIEW_SIZE)
        out_dir = Path(PREVIEW_DIR) / camera_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = str(out_dir / f"{epoch}.jpg")
        cv2.imwrite(path, small, [cv2.IMWRITE_JPEG_QUALITY, 60])
        return path
    except Exception as e:
        logger.error(f"[Preview] Failed: {e}")
        return None
