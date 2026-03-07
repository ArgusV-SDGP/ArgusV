"""
util/image.py — Image processing utilities
-------------------------------------------
Frigate equivalent: frigate/util/image.py

Common image manipulation helpers used across workers.
"""

import base64
import cv2
import numpy as np
from typing import Optional


def frame_to_b64(frame: np.ndarray, quality: int = 85) -> str:
    """Encode OpenCV frame to base64 JPEG string."""
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf).decode()


def b64_to_frame(b64: str) -> Optional[np.ndarray]:
    """Decode base64 JPEG string to OpenCV frame."""
    try:
        buf   = base64.b64decode(b64)
        arr   = np.frombuffer(buf, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        return None


def crop_bbox(frame: np.ndarray, bbox: dict, padding: int = 20) -> np.ndarray:
    """Crop bounding box region with padding."""
    h, w = frame.shape[:2]
    x1 = max(0, int(bbox.get("x1", 0)) - padding)
    y1 = max(0, int(bbox.get("y1", 0)) - padding)
    x2 = min(w, int(bbox.get("x2", w)) + padding)
    y2 = min(h, int(bbox.get("y2", h)) + padding)
    return frame[y1:y2, x1:x2]


def draw_detections(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    """Draw bounding boxes and labels on frame."""
    COLORS = {
        "HIGH":   (60,  60,  239),
        "MEDIUM": (50,  115, 249),
        "LOW":    (50,  197, 34),
        None:     (200, 200, 200),
    }
    out = frame.copy()
    for det in detections:
        bbox  = det.get("bbox", {})
        x1, y1 = int(bbox.get("x1", 0)), int(bbox.get("y1", 0))
        x2, y2 = int(bbox.get("x2", 0)), int(bbox.get("y2", 0))
        label = f"{det.get('object_class','')} {det.get('confidence',0):.0%}"
        color = COLORS.get(det.get("threat_level"))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        cv2.putText(out, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return out


def normalise_bbox(bbox: dict, w: int, h: int) -> dict:
    """Convert pixel bbox to normalised 0..1 coordinates."""
    return {
        "x1": bbox["x1"] / w, "y1": bbox["y1"] / h,
        "x2": bbox["x2"] / w, "y2": bbox["y2"] / h,
    }
