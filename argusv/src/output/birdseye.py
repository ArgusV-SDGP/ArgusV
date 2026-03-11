"""
output/birdseye.py — Multi-camera composite view
------------------------------------------------
Task: WATCH-08

Stitches the latest frames from all active cameras into a single
grid for the dashboard.
"""

import cv2
import numpy as np
import logging
from typing import Dict

logger = logging.getLogger("birdseye")

# Global cache for latest frames
# This would be populated by the EdgeWorker or FrameBuffer
_latest_frames: Dict[str, np.ndarray] = {}

def update_birdseye_frame(camera_id: str, frame: np.ndarray):
    """Update the latest frame for a camera."""
    _latest_frames[camera_id] = frame

def get_birdseye_composite() -> np.ndarray:
    """
    Stitch active camera frames into a grid.
    Returns a JPEG-encoded byte array or a placeholder.
    """
    active_cams = list(_latest_frames.keys())
    if not active_cams:
        # Return a black placeholder 640x360
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(img, "No Active Cameras", (150, 180), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        _, jpeg = cv2.imencode('.jpg', img)
        return jpeg.tobytes()

    # Determine grid size (e.g. 2x2 for 4 cams, 3x3 for 9)
    n = len(active_cams)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    
    # Target resolution per tile (e.g. 640x360)
    tw, th = 640, 360
    grid = np.zeros((rows * th, cols * tw, 3), dtype=np.uint8)
    
    for i, cam_id in enumerate(active_cams):
        r = i // cols
        c = i % cols
        
        frame = _latest_frames[cam_id]
        if frame is not None:
            # Resize to fit tile
            resized = cv2.resize(frame, (tw, th))
        else:
            # Placeholder for offline camera
            resized = np.zeros((th, tw, 3), dtype=np.uint8)
            cv2.putText(resized, f"{cam_id} OFFLINE", (50, 180), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            
        # Draw label
        cv2.rectangle(resized, (0, 0), (150, 35), (0, 0, 0), -1)
        cv2.putText(resized, cam_id, (10, 25), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        grid[r*th:(r+1)*th, c*tw:(c+1)*tw] = resized

    _, jpeg = cv2.imencode('.jpg', grid)
    return jpeg.tobytes()
