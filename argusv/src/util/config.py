"""
util/config.py — Config validation and loading
------------------------------------------------
Frigate equivalent: frigate/util/config.py + frigate/config/

Loads and validates system configuration.
Frigate uses a YAML file; ArgusV uses env vars + DB.
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger("util.config")


def parse_cameras_config() -> list[dict]:
    """
    Parse CAMERAS env var (JSON array) or fall back to single CAMERA_ID.

    Single camera (simple):
      CAMERA_ID=cam-01
      RTSP_URL=rtsp://mediamtx:8554/cam-01

    Multi-camera (CAMERAS JSON):
      CAMERAS=[
        {"camera_id":"cam-01","rtsp_url":"rtsp://...","detect_fps":5},
        {"camera_id":"cam-02","rtsp_url":"rtsp://...","detect_fps":3}
      ]
    """
    cameras_json = os.getenv("CAMERAS")
    if cameras_json:
        try:
            cams = json.loads(cameras_json)
            logger.info(f"[Config] Loaded {len(cams)} cameras from CAMERAS env var")
            return cams
        except json.JSONDecodeError as e:
            logger.error(f"[Config] Invalid CAMERAS JSON: {e}")

    # Fall back to single camera
    camera_id = os.getenv("CAMERA_ID", "cam-01")
    rtsp_url  = os.getenv("RTSP_URL",  "rtsp://mediamtx:8554/cam-01")
    if rtsp_url:
        logger.info(f"[Config] Single camera: {camera_id} → {rtsp_url}")
        return [{"camera_id": camera_id, "rtsp_url": rtsp_url}]

    logger.warning("[Config] No cameras configured — set CAMERA_ID+RTSP_URL or CAMERAS env vars")
    return []


def validate_config() -> list[str]:
    """
    Validate critical config at startup.
    Returns list of warning messages.
    """
    warnings = []

    if not os.getenv("POSTGRES_URL"):
        warnings.append("POSTGRES_URL not set — database unavailable")
    if not os.getenv("REDIS_URL"):
        warnings.append("REDIS_URL not set — zone hot-reload unavailable")
    if not os.getenv("OPENAI_API_KEY"):
        warnings.append("OPENAI_API_KEY not set — VLM analysis disabled")
    if not os.getenv("RTSP_URL") and not os.getenv("CAMERAS"):
        warnings.append("No RTSP_URL or CAMERAS set — no cameras will start")

    return warnings
