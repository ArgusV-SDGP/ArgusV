"""
events/maintainer.py — Event lifecycle manager
-----------------------------------------------
Frigate equivalent: frigate/events/maintainer.py

Manages the full lifecycle of detection events:
  START → UPDATE → LOITERING → END

Persists events to DB, updates segments,
generates clips and thumbnails.
Tasks: PIPE-01, REC-13, REC-14
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("events.maintainer")


class EventMaintainer:
    """
    Receives detection events from bus.raw_detections.
    Manages event state: open / updating / ended.
    On END → finalize clip, set retain flag on segment.

    Equivalent to Frigate's EventProcessor.
    """

    def __init__(self):
        # track_id → event state
        self._open_events: dict[int, dict] = {}

    async def process(self, event: dict):
        event_type = event.get("event_type")
        track_id   = event.get("track_id")

        if event_type == "START":
            self._open_events[track_id] = {
                **event,
                "started_at":     time.time(),
                "last_seen":      time.time(),
                "frame_count":    1,
                "max_confidence": event.get("confidence", 0),
                "best_frame":     event.get("frame"), # NEW: Capture first frame
            }
            logger.debug(f"[Events] START track={track_id} class={event.get('object_class')}")

        elif event_type in ("UPDATE", "LOITERING"):
            if track_id in self._open_events:
                ev = self._open_events[track_id]
                ev["last_seen"]      = time.time()
                ev["frame_count"]   += 1
                ev["event_type"]     = event_type
                
                # WATCH-07: Update best frame if confidence is higher
                if event.get("confidence", 0) > ev["max_confidence"]:
                    ev["max_confidence"] = event["confidence"]
                    ev["best_frame"]     = event.get("frame")

        elif event_type == "END":
            ev = self._open_events.pop(track_id, None)
            if ev:
                ev["ended_at"] = time.time()
                await self._finalize_event(ev)

    async def _finalize_event(self, ev: dict):
        """
        Called when track disappears.
        Locks overlapping segments and updates Detection mappings.
        """
        from db.connection import get_db_session
        from db.models import Segment, Detection
        from sqlalchemy.future import select

        dwell = ev.get("dwell_sec", 0)
        camera_id = ev.get("camera_id")
        event_id = ev.get("event_id")
        # Handle time safely if keys exist
        started_at = datetime.utcfromtimestamp(ev.get("started_at", time.time()))
        ended_at = datetime.utcfromtimestamp(ev.get("ended_at", time.time()))

        logger.info(f"[Events] END track={ev.get('track_id')} "
                    f"class={ev.get('object_class')} dwell={dwell}s")

        # REC-13 & REC-15: Lock segments and update DB
        async with get_db_session() as db:
            stmt = select(Segment).where(
                Segment.camera_id == camera_id,
                Segment.end_time >= started_at,
                Segment.start_time <= ended_at
            )
            res = await db.execute(stmt)
            segments = res.scalars().all()
            
            for s in segments:
                s.locked = True
                s.has_detections = True
                s.detection_count = (s.detection_count or 0) + 1

            if event_id and segments:
                stmt_det = select(Detection).where(Detection.event_id == event_id)
                res_det = await db.execute(stmt_det)
                det = res_det.scalars().first()
                if det:
                    det.segment_id = segments[0].segment_id
                    det.thumbnail_url = f"/api/incidents/{event_id}/thumbnail.jpg"
                    
                    # WATCH-07: Save thumbnail to disk
                    best_frame = ev.get("best_frame")
                    if best_frame:
                        import base64
                        import os
                        thumb_path = f"recordings/thumbnails/{event_id}.jpg"
                        os.makedirs(os.path.dirname(thumb_path), exist_ok=True)
                        with open(thumb_path, "wb") as f:
                            f.write(base64.b64decode(best_frame))
                            
            await db.commit()

        # REC-13: trigger clip generation for this event
        from bus import bus
        await bus.clips.put({
            "action_type": "GENERATE_CLIP",
            "event_id": event_id,
            "camera_id": camera_id,
        })
        
        # Trigger Semantic Analysis (RAG vectoring)
        await bus.rag_indexing.put({
            "event_id": event_id,
            "camera_id": camera_id,
            "timestamp": started_at.isoformat()
        })

    def flush_all(self):
        """Call on shutdown to finalize all open events."""
        for track_id, ev in list(self._open_events.items()):
            logger.info(f"[Events] Flushing open event track={track_id}")
            asyncio.create_task(self._finalize_event(ev))
        self._open_events.clear()
