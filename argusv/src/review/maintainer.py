"""
review/maintainer.py — Review queue manager
---------------------------------------------
Frigate equivalent: frigate/review/maintainer.py

Frigate's "review" system = alert triage queue.
Detections that need human review are placed in a
ReviewSegment (a time window requiring attention).

In ArgusV this maps to:
  HIGH/MEDIUM threat Incidents → review queue
  Operator must RESOLVE or DISMISS each one.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger("review.maintainer")

# Review states
REVIEW_STATUS_PENDING   = "pending"
REVIEW_STATUS_REVIEWED  = "reviewed"
REVIEW_STATUS_DISMISSED = "dismissed"


class ReviewMaintainer:
    """
    Manages review segments — time windows of alert activity
    grouped per camera for operator review.

    Frigate groups nearby events into review segments:
    - Alert segment: HIGH/MEDIUM threat events
    - Detection segment: LOW / no-threat events (for audit)

    TODO: implement review segment grouping
    """

    def __init__(self):
        # camera_id → open alert segment
        self._open_segments: dict[str, dict] = {}

    async def process_event(self, event: dict):
        """
        Receives a vlm_result event.
        Groups into review segment by camera + time window.
        TODO DCFG-01: implement grouping logic
        """
        camera_id    = event.get("camera_id")
        is_alert     = event.get("is_threat") or event.get("threat_level") in ("HIGH", "MEDIUM")
        threat_level = event.get("threat_level", "LOW")
        timestamp    = event.get("timestamp")

        if is_alert:
            await self._open_or_extend_alert_segment(camera_id, event)

    async def _open_or_extend_alert_segment(self, camera_id: str, event: dict):
        """
        Create a new review segment or extend existing one if within
        REVIEW_SEGMENT_GAP_SEC seconds.
        TODO: implement
        """
        # TODO: group events within 30s window into one ReviewSegment
        # TODO: write ReviewSegment to DB
        # TODO: push to bus.alerts_ws with type="review_update"
        logger.debug(f"[Review] TODO: group event into review segment for {camera_id}")


async def review_maintainer_worker(vlm_results_queue: asyncio.Queue):
    """
    Reads from bus.vlm_results (tap) and processes review segments.
    TODO: wire into api/server.py lifespan
    """
    maintainer = ReviewMaintainer()
    logger.info("[Review] Worker started")
    while True:
        event = await vlm_results_queue.get()
        try:
            await maintainer.process_event(event)
        except Exception as e:
            logger.error(f"[Review] Error: {e}")
        finally:
            vlm_results_queue.task_done()
