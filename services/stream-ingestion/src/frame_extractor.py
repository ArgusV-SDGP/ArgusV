"""Grab a small set of context frames from an RTSP source."""

from __future__ import annotations

import time
from typing import Dict, Iterable

import cv2


def extract_frames(
    rtsp_url: str,
    offsets: Iterable[float] = (0, 1, 2),
    connect_timeout: float = 10.0,
    wait_step: float = 0.05,
) -> Dict[float, bytes]:
    """
    Connects to an RTSP feed, captures samples at the requested offsets (in seconds),
    and returns a mapping from offset to JPEG bytes.
    """
    offsets = sorted({float(offset) for offset in offsets if offset >= 0})
    if not offsets:
        return {}

    capture = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    start_time = time.time()
    deadline = start_time + connect_timeout

    if not capture.isOpened():
        print(f"[frame_extractor] Unable to open stream {rtsp_url}")
        capture.release()
        return {}

    frames: Dict[float, bytes] = {}
    try:
        for offset in offsets:
            target_time = start_time + offset
            if time.time() > deadline:
                print("[frame_extractor] Read deadline exceeded, stopping capture.")
                break

            while time.time() < target_time:
                capture.grab()
                time.sleep(wait_step)

            success, frame = capture.read()
            if not success:
                print(f"[frame_extractor] Failed to read frame at {offset}s")
                break

            success, encoded = cv2.imencode(".jpg", frame)
            if not success:
                print(f"[frame_extractor] Encoding failed for offset {offset}")
                continue

            frames[offset] = encoded.tobytes()
    finally:
        capture.release()

    return frames
s