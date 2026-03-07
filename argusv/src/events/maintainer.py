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
            }
            logger.debug(f"[Events] START track={track_id} class={event.get('object_class')}")

        elif event_type in ("UPDATE", "LOITERING"):
            if track_id in self._open_events:
                ev = self._open_events[track_id]
                ev["last_seen"]      = time.time()
                ev["frame_count"]   += 1
                ev["max_confidence"] = max(ev["max_confidence"], event.get("confidence", 0))
                ev["dwell_sec"]      = event.get("dwell_sec", 0)
                ev["event_type"]     = event_type

        elif event_type == "END":
            ev = self._open_events.pop(track_id, None)
            if ev:
                ev["ended_at"] = time.time()
                await self._finalize_event(ev)

    async def _finalize_event(self, ev: dict):
        """
        Called when track disappears.
        TODO: lock segment, generate clip, update Detection row.
        """
        dwell = ev.get("dwell_sec", 0)
        logger.info(f"[Events] END track={ev.get('track_id')} "
                    f"class={ev.get('object_class')} dwell={dwell}s")
        # TODO REC-13: trigger clip generation for this event
        # TODO REC-15: ensure thumbnail_url is set on Detection row
        # TODO: set Segment.locked=True for segments covering this event

    def flush_all(self):
        """Call on shutdown to finalize all open events."""
        for track_id, ev in list(self._open_events.items()):
            logger.info(f"[Events] Flushing open event track={track_id}")
            asyncio.create_task(self._finalize_event(ev))
        self._open_events.clear()
