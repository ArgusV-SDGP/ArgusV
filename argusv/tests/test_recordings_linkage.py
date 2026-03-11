import pytest
import asyncio
from datetime import datetime, timedelta
import uuid

# Provide local test dependencies
import os
os.environ["POSTGRES_URL"] = "sqlite:///:memory:"

from db.models import Base, Segment, Detection, Incident
from db.connection import _sync_engine, get_db_sync, get_db_session

from events.maintainer import EventMaintainer
import time

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(_sync_engine)
    yield
    Base.metadata.drop_all(_sync_engine)

@pytest.mark.asyncio
async def test_maintainer_links_segments():
    # 1. Create a segment in the DB
    start_time = datetime.utcnow()
    end_time = start_time + timedelta(seconds=10)
    
    db = get_db_sync()
    seg = Segment(
        camera_id="cam-test",
        start_time=start_time,
        end_time=end_time,
        duration_sec=10,
        minio_path="/tmp/test.ts"
    )
    db.add(seg)
    db.commit()
    
    seg_id = seg.segment_id

    # 2. Add an unlinked detection that occurred DURING that segment
    det = Detection(
        event_id="evt-123",
        camera_id="cam-test",
        # Detected at 5 seconds into the 10 second segment
        detected_at=start_time + timedelta(seconds=5), 
        object_class="person",
        confidence=0.9
    )
    db.add(det)
    db.commit()

    # Verify detection has NO segment initially
    assert det.segment_id is None

    # 3. Simulate the EventMaintainer receiving the END event
    maintainer = EventMaintainer()
    
    event = {
        "event_type": "END",
        "track_id": 99,
        "event_id": "evt-123",
        "camera_id": "cam-test",
        "object_class": "person",
        "confidence": 0.9,
        "started_at": start_time.timestamp(),
        "ended_at": end_time.timestamp(),
        "dwell_sec": 10
    }
    
    # Process the fake "END" event natively
    await maintainer._finalize_event(event)

    # 4. Check that the DB was properly updated!
    db.refresh(seg)
    db.refresh(det)

    # Segment should now be marked locked so the cleaner ignores it
    assert seg.locked is True 
    assert seg.has_detections is True
    assert seg.detection_count == 1

    # Detection should now be linked securely to the Segment ID
    assert det.segment_id == seg_id
    assert det.thumbnail_url == "/api/incidents/evt-123/thumbnail.jpg"

    db.close()
