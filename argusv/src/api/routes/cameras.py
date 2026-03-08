"""api/routes/cameras.py — Camera CRUD and runtime updates."""

from datetime import datetime, timezone
import json
import logging
import uuid
from typing import Any, Optional

import redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Camera

router = APIRouter(prefix="/api/cameras", tags=["cameras"])
logger = logging.getLogger("api.cameras")


class CameraCreate(BaseModel):
    camera_id: str
    name: str
    rtsp_url: str
    zone_id: Optional[str] = None
    status: str = "online"
    resolution: Optional[str] = None
    fps: int = 25


class CameraPatch(BaseModel):
    name: Optional[str] = None
    rtsp_url: Optional[str] = None
    zone_id: Optional[str] = None
    status: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None


def _serialize_camera(c: Camera) -> dict[str, Any]:
    return {
        "camera_id": c.camera_id,
        "name": c.name,
        "rtsp_url": c.rtsp_url,
        "zone_id": str(c.zone_id) if c.zone_id else None,
        "status": c.status,
        "resolution": c.resolution,
        "fps": c.fps,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "last_seen": c.last_seen.isoformat() if c.last_seen else None,
    }


def _publish_camera_update(camera_id: str, action: str) -> None:
    payload = {
        "type": "CAMERA_UPDATE",
        "action": action,
        "camera_id": camera_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = redis.from_url(cfg.REDIS_URL, decode_responses=True)
        raw = json.dumps(payload)
        r.publish("config-updates", raw)
    except Exception as e:
        logger.warning(f"Failed to publish camera update for {camera_id}: {e}")


@router.get("")
def list_cameras(
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    cams = db.query(Camera).order_by(Camera.camera_id.asc()).all()
    return [_serialize_camera(c) for c in cams]


@router.get("/{camera_id}")
def get_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    cam = db.query(Camera).filter(Camera.camera_id == camera_id).first()
    if not cam:
        raise HTTPException(404, "Camera not found")
    return _serialize_camera(cam)


@router.post("", status_code=201)
def create_camera(
    payload: CameraCreate,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    exists = db.query(Camera).filter(Camera.camera_id == payload.camera_id).first()
    if exists:
        raise HTTPException(409, "camera_id already exists")

    zone_id = None
    if payload.zone_id:
        try:
            zone_id = uuid.UUID(payload.zone_id)
        except ValueError:
            raise HTTPException(400, "Invalid zone_id")

    cam = Camera(
        camera_id=payload.camera_id,
        name=payload.name,
        rtsp_url=payload.rtsp_url,
        zone_id=zone_id,
        status=payload.status,
        resolution=payload.resolution,
        fps=payload.fps,
        last_seen=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(cam)
    db.commit()
    db.refresh(cam)
    _publish_camera_update(cam.camera_id, "created")
    return _serialize_camera(cam)


@router.put("/{camera_id}")
def update_camera(
    camera_id: str,
    payload: CameraPatch,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    cam = db.query(Camera).filter(Camera.camera_id == camera_id).first()
    if not cam:
        raise HTTPException(404, "Camera not found")

    data = payload.dict(exclude_unset=True)
    if "zone_id" in data and data["zone_id"] is not None:
        try:
            data["zone_id"] = uuid.UUID(data["zone_id"])
        except ValueError:
            raise HTTPException(400, "Invalid zone_id")
    for key, value in data.items():
        setattr(cam, key, value)
    cam.last_seen = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(cam)
    _publish_camera_update(cam.camera_id, "updated")
    return _serialize_camera(cam)


@router.delete("/{camera_id}", status_code=204)
def delete_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    cam = db.query(Camera).filter(Camera.camera_id == camera_id).first()
    if not cam:
        raise HTTPException(404, "Camera not found")

    db.delete(cam)
    db.commit()
    _publish_camera_update(camera_id, "deleted")
