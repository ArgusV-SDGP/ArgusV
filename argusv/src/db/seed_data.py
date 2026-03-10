"""
db/seed_data.py — Database seeder for Dev 5/6 testing
------------------------------------------------------
Tasks: WATCH-06, DB-07

Populates:
  - Mock cameras
  - Mock zones (with polygons)
  - Mock detections & incidents
"""

import uuid
from datetime import datetime, timedelta
from db.connection import get_db_sync
from db.models import Camera, Zone, Detection, Incident

def seed_database():
    db = get_db_sync()
    try:
        # 1. Cameras
        cam_id = "cam-01"
        if not db.query(Camera).filter(Camera.camera_id == cam_id).first():
            db.add(Camera(
                camera_id=cam_id,
                name="Front Entry",
                rtsp_url="rtsp://localhost:8554/cam-01",
                is_active=True
            ))
            logger_info(f"Seeded camera: {cam_id}")

        # 2. Zones
        zone_name = "Driveway"
        if not db.query(Zone).filter(Zone.name == zone_name).first():
            db.add(Zone(
                camera_id=cam_id,
                name=zone_name,
                polygon=[[10, 10], [100, 10], [100, 100], [10, 100]],
                is_active=True
            ))
            logger_info(f"Seeded zone: {zone_name}")

        # 3. Detections
        det_id = str(uuid.uuid4())
        db.add(Detection(
            detection_id=det_id,
            camera_id=cam_id,
            zone_name=zone_name,
            object_class="person",
            confidence=0.92,
            is_threat=False,
            detected_at=datetime.utcnow() - timedelta(minutes=5)
        ))

        # 4. Incident
        db.add(Incident(
            incident_id=uuid.uuid4(),
            camera_id=cam_id,
            zone_name=zone_name,
            object_class="person",
            threat_level="LOW",
            summary="Person detected in driveway",
            status="OPEN",
            detected_at=datetime.utcnow() - timedelta(minutes=5)
        ))
        
        db.commit()
        print("✅ Database seeding complete")
    except Exception as e:
        db.rollback()
        print(f"❌ Seeding failed: {e}")
    finally:
        db.close()

def logger_info(msg):
    print(f"[Seed] {msg}")

if __name__ == "__main__":
    seed_database()
