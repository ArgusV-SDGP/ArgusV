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
    logger.info(f"🧹 [Cleanup] Worker started — runs every {CLEANUP_INTERVAL_HOURS}h")
    while True:
        try:
            # We run in a separate thread so sync DB calls don't block the loop
            await asyncio.to_thread(_cleanup_segments)
            await asyncio.to_thread(_auto_resolve_incidents)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[Cleanup] Error: {e}", exc_info=True)
            
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)


def _cleanup_segments():
    """
    Delete segments older than retain_until from disk + DB.
    Skips locked segments (which are retained infinitely for incidents).
    Task REC-10, REC-11
    """
    from db.connection import get_db_sync
    from db.models import Segment
    
    cutoff = datetime.utcnow() - timedelta(days=cfg.RECORDINGS_RETAIN_DAYS)
    
    db = get_db_sync()
    try:
        # Search for unlocked segments past the cutoff
        old_segments = db.query(Segment).filter(
            Segment.start_time < cutoff,
            Segment.locked == False,
        ).all()
        
        deleted_count = 0
        for seg in old_segments:
            # 1. Delete the physical .ts file from disk
            path = Path(seg.minio_path)
            if path.exists():
                try:
                    path.unlink()
                except Exception as e:
                    logger.error(f"[Cleanup] Could not delete disk file {path}: {e}")
                    continue
            
            # 2. Delete the row from the database
            db.delete(seg)
            deleted_count += 1
            
        db.commit()
        if deleted_count > 0:
            logger.info(f"🧹 [Cleanup] Deleted {deleted_count} old unlocked video segments (older than {cfg.RECORDINGS_RETAIN_DAYS} days).")
    except Exception as e:
        logger.error(f"[Cleanup] Failed during segment cleanup: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def _auto_resolve_incidents():
    """
    Auto-resolve OPEN incidents older than INCIDENT_RESOLVE_DAYS.
    Task DB-09
    """
    from db.connection import get_db_sync
    from db.models import Incident
    
    cutoff = datetime.utcnow() - timedelta(days=INCIDENT_RESOLVE_DAYS)
    
    db = get_db_sync()
    try:
        incidents_to_close = db.query(Incident).filter(
            Incident.status == "OPEN",
            Incident.detected_at < cutoff,
        ).all()
        
        count = 0
        for inc in incidents_to_close:
            inc.status = "RESOLVED"
            inc.resolved_at = datetime.utcnow()
            count += 1
            
        db.commit()
        if count > 0:
            logger.info(f"🧹 [Cleanup] Auto-resolved {count} stale incidents (older than {INCIDENT_RESOLVE_DAYS} days).")
    except Exception as e:
        logger.error(f"[Cleanup] Failed during incident resolution: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()
