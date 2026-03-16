"""
db/seed.py — Seed default data for development
Task: DB-07
"""

# TODO DB-07: seed default zone + test camera

import logging
from sqlalchemy.orm import Session
from db.models import Camera, Zone
from db.connection import get_db_sync
import uuid

logger = logging.getLogger("db.seed")

def seed_dev_data():
    """Insert sensible defaults for local development."""
    db: Session = get_db_sync()
    try:
        dev_cameras = [
            {
                "camera_id": "cam-01",
                "name": "Dev Camera",
                "rtsp_url": "rtsp://mediamtx:8554/cam-01",
            },
            {
                "camera_id": "cam-02",
                "name": "Dev Camera 2",
                "rtsp_url": "rtsp://mediamtx:8554/cam-02",
            },
        ]

        for dev_cam in dev_cameras:
            cam = db.query(Camera).filter_by(camera_id=dev_cam["camera_id"]).first()
            if cam:
                cam.name = dev_cam["name"]
                cam.rtsp_url = dev_cam["rtsp_url"]
                cam.status = "online"
                cam.resolution = "1280x720"
                cam.fps = 25
                logger.info(f"[Seed] Updated default camera: {dev_cam['camera_id']}")
            else:
                db.add(Camera(
                    camera_id=dev_cam["camera_id"],
                    name=dev_cam["name"],
                    rtsp_url=dev_cam["rtsp_url"],
                    status="online",
                    resolution="1280x720",
                    fps=25,
                ))
                logger.info(f"[Seed] Inserted default camera: {dev_cam['camera_id']}")

        # Default full-frame zone
        if not db.query(Zone).filter_by(name="Full Frame").first():
            db.add(Zone(
                zone_id              = uuid.uuid4(),
                name                 = "Full Frame",
                polygon_coords       = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                dwell_threshold_sec  = 30,
                active               = True,
            ))
            logger.info("[Seed] Inserted default zone: Full Frame")

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[Seed] Failed: {e}")
    finally:
        db.close()
