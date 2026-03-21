"""
db/seed.py — Seed default data for development
Task: DB-07

Seeds cameras from cfg.CAMERAS so the DB always matches the configured
camera list. Also creates a default "Full Frame" zone.
"""

import logging
import uuid

from sqlalchemy.orm import Session

import config as cfg
from db.connection import get_db_sync
from db.models import Camera, Zone

logger = logging.getLogger("db.seed")


def seed_dev_data():
    """Insert sensible defaults for local development."""
    db: Session = get_db_sync()
    try:
        # ── Cameras — driven by cfg.CAMERAS ────────────────────────────────
        for cam_cfg in cfg.CAMERAS:
            cam_id = cam_cfg["id"]
            existing = db.query(Camera).filter_by(camera_id=cam_id).first()
            if existing:
                # Update rtsp_url if it changed (e.g. switched RTSP_HOST)
                if existing.rtsp_url != cam_cfg["rtsp_url"]:
                    existing.rtsp_url = cam_cfg["rtsp_url"]
                    logger.info(f"[Seed] Updated rtsp_url for {cam_id}")
                if cam_cfg.get("name") and existing.name != cam_cfg["name"]:
                    existing.name = cam_cfg["name"]
                continue
            db.add(Camera(
                camera_id  = cam_id,
                name       = cam_cfg.get("name", cam_id),
                rtsp_url   = cam_cfg["rtsp_url"],
                status     = "online",
                resolution = cam_cfg.get("resolution", "1280x720"),
                fps        = cam_cfg.get("fps", 25),
            ))
            logger.info(f"[Seed] Inserted camera: {cam_id}")

        # ── Default full-frame zone ────────────────────────────────────────
        if not db.query(Zone).filter_by(name="Full Frame").first():
            db.add(Zone(
                zone_id             = uuid.uuid4(),
                name                = "Full Frame",
                polygon_coords      = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                dwell_threshold_sec = 30,
                active              = True,
            ))
            logger.info("[Seed] Inserted default zone: Full Frame")

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[Seed] Failed: {e}")
    finally:
        db.close()
