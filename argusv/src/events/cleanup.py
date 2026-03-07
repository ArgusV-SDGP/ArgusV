"""
events/cleanup.py — Event data cleanup
---------------------------------------
Frigate equivalent: frigate/events/cleanup.py

Periodically removes old event data from DB.
Runs as a background task.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger("events.cleanup")

EVENT_RETAIN_DAYS = int(os.getenv("EVENT_RETAIN_DAYS", "30"))


async def event_cleanup_worker():
    """
    Runs every 24 hours.
    Deletes ended events (Detection rows) older than EVENT_RETAIN_DAYS.
    Preserves locked (incident-linked) events.
    TODO DB-09: implement
    """
    logger.info(f"[Events.Cleanup] Worker started — retain {EVENT_RETAIN_DAYS} days")
    while True:
        try:
            await _cleanup_old_events()
        except Exception as e:
            logger.error(f"[Events.Cleanup] Error: {e}", exc_info=True)
        await asyncio.sleep(24 * 3600)


async def _cleanup_old_events():
    # TODO: implement
    # cutoff = datetime.utcnow() - timedelta(days=EVENT_RETAIN_DAYS)
    # db.query(Detection).filter(Detection.detected_at < cutoff, ...).delete()
    logger.debug("[Events.Cleanup] TODO: implement old event cleanup")
