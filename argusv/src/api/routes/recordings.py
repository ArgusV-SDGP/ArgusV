"""
api/routes/recordings.py — NVR Replay API
Tasks: API-12, API-13, API-14, API-15
"""
# TODO API-12: GET /api/recordings/{cam} — segment list
# TODO API-13: GET /api/recordings/{cam}/playlist — HLS m3u8
# TODO API-14: GET /api/recordings/{cam}/timeline — detection markers
# TODO API-15: GET /api/incidents/{id}/replay — incident clip

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
from db.connection import get_db
from db.models import Segment, Detection, Incident

router = APIRouter(tags=["recordings"])


@router.get("/api/recordings/{camera_id}")
def list_segments(
    camera_id: str,
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    only_events: bool = Query(False),
    db: Session = Depends(get_db),
):
    # TODO API-12
    raise HTTPException(501, "Not implemented yet — see API-12")


@router.get("/api/recordings/{camera_id}/playlist")
def hls_playlist(
    camera_id: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
):
    # TODO API-13
    raise HTTPException(501, "Not implemented yet — see API-13")


@router.get("/api/recordings/{camera_id}/timeline")
def detection_timeline(
    camera_id: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    threats_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    # TODO API-14
    raise HTTPException(501, "Not implemented yet — see API-14")


@router.get("/api/incidents/{incident_id}/replay")
def incident_replay(
    incident_id: str,
    padding_sec: int = Query(15),
    db: Session = Depends(get_db),
):
    # TODO API-15
    raise HTTPException(501, "Not implemented yet — see API-15")
