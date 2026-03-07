"""
output/birdseye.py — BirdsEye overview renderer
-------------------------------------------------
Frigate equivalent: frigate/output/birdseye.py

Renders a top-down "birds-eye" view showing all
camera zones and active tracked objects in one frame.

In ArgusV this can be:
  1. A canvas API endpoint → GET /api/birdseye (JPEG/PNG)
  2. A WebSocket stream showing live positions
  3. A static SVG diagram updated on each detection

TODO DLIVE-08: implement
"""

import logging
import cv2
import numpy as np

logger = logging.getLogger("output.birdseye")


class BirdsEyeRenderer:
    """
    Renders a composite overview of all camera zones.
    Active tracks appear as coloured dots.

    Frigate: renders a physical floorplan + camera positions.
    ArgusV simplification: grid of zone rectangles with active objects.

    TODO DLIVE-08
    """

    WIDTH  = 1280
    HEIGHT = 720
    BG     = (18, 20, 26)   # dark background

    def __init__(self, cameras: list[dict]):
        self.cameras  = cameras
        self._frame   = np.full((self.HEIGHT, self.WIDTH, 3), self.BG, dtype=np.uint8)
        self._objects: dict[int, dict] = {}   # track_id → {x,y,label,threat}

    def update_object(self, track_id: int, norm_x: float, norm_y: float,
                      label: str, threat_level: str):
        """Update tracked object position on birdseye canvas."""
        self._objects[track_id] = {
            "x": norm_x, "y": norm_y,
            "label": label, "threat": threat_level,
            "last_seen": 0,
        }

    def remove_object(self, track_id: int):
        self._objects.pop(track_id, None)

    def render(self) -> bytes:
        """Render current frame as JPEG bytes."""
        frame = np.full((self.HEIGHT, self.WIDTH, 3), self.BG, dtype=np.uint8)

        # Draw grid of camera zones
        # TODO DLIVE-08: draw actual zone polygons per camera
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
