"""
output/birdseye.py — BirdsEye overview renderer
-------------------------------------------------
Renders a top-down "birds-eye" view showing all
camera zones and active tracked objects in one frame.

GET /api/birdseye → JPEG snapshot of current positions.
"""

import logging
import time
import cv2
import numpy as np

logger = logging.getLogger("output.birdseye")

# Tracks older than this (seconds) are evicted automatically on render
_STALE_TRACK_SEC = 30


class BirdsEyeRenderer:
    """
    Renders a composite overview of all camera zones.
    Active tracks appear as coloured dots.
    """

    WIDTH  = 1280
    HEIGHT = 720
    BG     = (18, 20, 26)   # dark background

    def __init__(self, cameras: list[dict]):
        self.cameras  = cameras
        self._objects: dict[int, dict] = {}   # track_id → {x,y,label,threat,last_seen}

    def update_object(self, track_id: int, norm_x: float, norm_y: float,
                      label: str, threat_level: str):
        """Update tracked object position on birdseye canvas."""
        self._objects[track_id] = {
            "x": norm_x, "y": norm_y,
            "label": label, "threat": threat_level,
            "last_seen": time.monotonic(),
        }

    def remove_object(self, track_id: int):
        self._objects.pop(track_id, None)

    def _evict_stale(self):
        """Remove tracks that have not been updated within _STALE_TRACK_SEC."""
        cutoff = time.monotonic() - _STALE_TRACK_SEC
        stale = [tid for tid, obj in self._objects.items() if obj["last_seen"] < cutoff]
        for tid in stale:
            del self._objects[tid]

    def render(self) -> bytes:
        """Render current frame as JPEG bytes."""
        self._evict_stale()

        frame = np.full((self.HEIGHT, self.WIDTH, 3), self.BG, dtype=np.uint8)

        # Draw grid of camera zones
        n = max(len(self.cameras), 1)
        cols = min(n, 3)
        rows = (n + cols - 1) // cols
        cw = self.WIDTH  // cols
        ch = self.HEIGHT // rows

        for i, cam in enumerate(self.cameras):
            r, c = divmod(i, cols)
            x1, y1 = c * cw, r * ch
            cv2.rectangle(frame, (x1+4, y1+4), (x1+cw-4, y1+ch-4), (40, 50, 70), -1)
            cv2.rectangle(frame, (x1+4, y1+4), (x1+cw-4, y1+ch-4), (80, 100, 130), 1)
            cv2.putText(frame, cam.get("camera_id", "cam"),
                        (x1+12, y1+24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 180, 220), 1)

        # Draw active tracked objects
        THREAT_COLORS = {"HIGH": (60, 60, 239), "MEDIUM": (50, 115, 249), "LOW": (50, 197, 34)}
        for tid, obj in list(self._objects.items()):
            px = int(obj["x"] * self.WIDTH)
            py = int(obj["y"] * self.HEIGHT)
            color = THREAT_COLORS.get(obj["threat"], (128, 128, 128))
            cv2.circle(frame, (px, py), 8, color, -1)
            cv2.putText(frame, f"{obj['label']} #{tid}",
                        (px+10, py+5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes()
