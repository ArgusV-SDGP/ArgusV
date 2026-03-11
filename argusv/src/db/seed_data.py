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
import random
from db.connection import get_db_sync
from db.models import Camera, Zone, Detection, Incident, Segment

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

        # 3. Detections & Incidents (Multiple over last 2 hours)
        objects = ["person", "car", "dog"]
        now = datetime.utcnow()
        for i in range(15):
            past_time = now - timedelta(minutes=random.randint(1, 120))
            det_id = str(uuid.uuid4())
            obj = random.choice(objects)
            conf = round(random.uniform(0.6, 0.98), 2)
            is_threat = (obj == "person" and conf > 0.8)
            
            db.add(Detection(
                detection_id=det_id,
                camera_id=cam_id,
                zone_name=zone_name,
                object_class=obj,
                confidence=conf,
                is_threat=is_threat,
                detected_at=past_time,
                bbox_x1=random.randint(0, 500),
                bbox_y1=random.randint(0, 500),
                bbox_x2=random.randint(501, 1000),
                bbox_y2=random.randint(501, 1000)
            ))

            if is_threat or random.random() > 0.8:
                db.add(Incident(
                    incident_id=uuid.uuid4(),
                    camera_id=cam_id,
                    zone_name=zone_name,
                    object_class=obj,
                    threat_level="MEDIUM" if is_threat else "LOW",
                    summary=f"Detected {obj} with {conf} confidence",
                    status="OPEN" if is_threat else "RESOLVED",
                    detected_at=past_time
                ))

        # 4. Mock Segments (to test replay)
        for i in range(10):
            seg_start = now - timedelta(minutes=(i+1)*5)
            seg_end = seg_start + timedelta(minutes=1)
            db.add(Segment(
                camera_id=cam_id,
                start_time=seg_start,
                end_time=seg_end,
                file_path=f"/recordings/cam-01/{seg_start.strftime('%Y%m%d%H%M%S')}.ts",
                duration=60.0,
                file_size=5_000_000,
                has_detections=(i % 2 == 0)
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
