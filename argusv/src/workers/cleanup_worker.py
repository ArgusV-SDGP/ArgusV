"""
workers/cleanup_worker.py — Storage retention + cleanup
---------------------------------------------------------
Tasks: REC-10, REC-11, PIPE-12, DB-09

Runs on a schedule (every CLEANUP_INTERVAL_HOURS hours).
Deletes segments older than RECORDINGS_RETAIN_DAYS.
Auto-resolves open incidents older than INCIDENT_RESOLVE_DAYS.
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import config as cfg

logger = logging.getLogger("cleanup-worker")

CLEANUP_INTERVAL_HOURS = int(os.getenv("CLEANUP_INTERVAL_HOURS", "6"))
INCIDENT_RESOLVE_DAYS  = int(os.getenv("INCIDENT_RESOLVE_DAYS",  "30"))
LOCAL_RECORDINGS_DIR   = os.getenv("LOCAL_RECORDINGS_DIR", "/recordings")


async def cleanup_worker():
    """Background task: runs cleanup every CLEANUP_INTERVAL_HOURS."""
    logger.info(f"[Cleanup] Worker started — runs every {CLEANUP_INTERVAL_HOURS}h")
    while True:
        try:
            await _cleanup_segments()
            await _auto_resolve_incidents()
        except Exception as e:
            logger.error(f"[Cleanup] Error: {e}", exc_info=True)
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)


async def _cleanup_segments():
    """
    Delete segments older than retain_until from disk + DB.
    Skips locked segments.
    Task REC-10, REC-11
    """
    # TODO REC-10: implement
    # from db.connection import get_db_sync
    # from db.models import Segment
    # cutoff = datetime.utcnow() - timedelta(days=cfg.RECORDINGS_RETAIN_DAYS)
    # db = get_db_sync()
    # try:
    #     old = db.query(Segment).filter(
    #         Segment.start_time < cutoff,
    #         Segment.locked == False,
    #     ).all()
    #     for seg in old:
    #         path = Path(seg.minio_path)
    #         if path.exists():
    #             path.unlink()
    #         db.delete(seg)
    #     db.commit()
    #     logger.info(f"[Cleanup] Deleted {len(old)} old segments")
    # finally:
    #     db.close()
    logger.debug("[Cleanup] TODO REC-10: segment cleanup (not yet implemented)")


async def _auto_resolve_incidents():
    """
    Auto-resolve OPEN incidents older than INCIDENT_RESOLVE_DAYS.
    Task DB-09
    """
    # TODO DB-09: implement
    # from db.connection import get_db_sync
    # from db.models import Incident
    # cutoff = datetime.utcnow() - timedelta(days=INCIDENT_RESOLVE_DAYS)
    # db = get_db_sync()
    # try:
    #     updated = db.query(Incident).filter(
    #         Incident.status == "OPEN",
    #         Incident.detected_at < cutoff,
    #     ).update({"status": "RESOLVED", "resolved_at": datetime.utcnow()})
    #     db.commit()
    #     logger.info(f"[Cleanup] Auto-resolved {updated} old incidents")
    # finally:
    #     db.close()
    logger.debug("[Cleanup] TODO DB-09: auto-resolve old incidents")
