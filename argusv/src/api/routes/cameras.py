"""api/routes/cameras.py — Camera CRUD and runtime updates."""

from datetime import datetime, timezone
import json
import logging
import re
import uuid
from typing import Any, Optional, Literal

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Camera, Rule, Zone

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


def _parse_resolution(resolution: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not resolution:
        return None, None
    m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", resolution)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _compute_bbox(polygon_coords: list[list[float]]) -> Optional[dict[str, float]]:
    if not polygon_coords:
        return None
    xs = [float(p[0]) for p in polygon_coords]
    ys = [float(p[1]) for p in polygon_coords]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    w = x2 - x1
    h = y2 - y1
    return {
        "x1": round(x1, 6),
        "y1": round(y1, 6),
        "x2": round(x2, 6),
        "y2": round(y2, 6),
        "w": round(w, 6),
        "h": round(h, 6),
        "cx": round(x1 + (w / 2), 6),
        "cy": round(y1 + (h / 2), 6),
        "area": round(w * h, 6),
    }


def _bbox_to_px(bbox_norm: Optional[dict[str, float]], frame_w: Optional[int], frame_h: Optional[int]) -> Optional[dict[str, int]]:
    if not bbox_norm or not frame_w or not frame_h:
        return None
    x1 = int(round(bbox_norm["x1"] * frame_w))
    y1 = int(round(bbox_norm["y1"] * frame_h))
    x2 = int(round(bbox_norm["x2"] * frame_w))
    y2 = int(round(bbox_norm["y2"] * frame_h))
    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "w": max(0, x2 - x1),
        "h": max(0, y2 - y1),
        "cx": int(round((x1 + x2) / 2)),
        "cy": int(round((y1 + y2) / 2)),
        "area": max(0, x2 - x1) * max(0, y2 - y1),
    }


def _serialize_zone_for_camera(zone: Zone, frame_w: Optional[int], frame_h: Optional[int], rules: list[Rule]) -> dict[str, Any]:
    bbox_norm = _compute_bbox(zone.polygon_coords or [])
    bbox_px = _bbox_to_px(bbox_norm, frame_w, frame_h)
    return {
        "zone_id": str(zone.zone_id),
        "name": zone.name,
        "polygon_coords": zone.polygon_coords,
        "zone_type": zone.zone_type,
        "dwell_threshold_sec": zone.dwell_threshold_sec,
        "active": zone.active,
        "created_at": zone.created_at.isoformat() if zone.created_at else None,
        "updated_at": zone.updated_at.isoformat() if zone.updated_at else None,
        "bbox_norm": bbox_norm,
        "bbox_px": bbox_px,
        "rule_count": len(rules),
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


@router.get("/{camera_id}/zones")
def list_camera_zones(
    camera_id: str,
    view: Literal["with_zones", "without_zones"] = Query("with_zones"),
    selection: Literal["assigned", "all"] = Query("all"),
    zone_ids: Optional[str] = Query(None, description="Comma-separated zone UUIDs to include"),
    active_only: bool = Query(True),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    cam = db.query(Camera).filter(Camera.camera_id == camera_id).first()
    if not cam:
        raise HTTPException(404, "Camera not found")

    frame_w, frame_h = _parse_resolution(cam.resolution)

    query = db.query(Zone)
    if active_only:
        query = query.filter(Zone.active == True)
    if selection == "assigned" and cam.zone_id:
        query = query.filter(Zone.zone_id == cam.zone_id)

    candidate_zones = query.order_by(Zone.created_at.desc()).all()
    available_zone_ids = [str(z.zone_id) for z in candidate_zones]

    selected_zone_ids: list[str] = []
    if zone_ids:
        selected_zone_ids = [z.strip() for z in zone_ids.split(",") if z.strip()]
    selected_zone_id_set = set(selected_zone_ids)

    zones = candidate_zones
    if selected_zone_id_set:
        zones = [z for z in zones if str(z.zone_id) in selected_zone_id_set]
    if view == "without_zones":
        zones = []

    zone_uuids = [z.zone_id for z in zones]
    rules_by_zone: dict[Any, list[Rule]] = {}
    if zone_uuids:
        all_rules = db.query(Rule).filter(Rule.zone_id.in_(zone_uuids)).all()
        for r in all_rules:
            rules_by_zone.setdefault(r.zone_id, []).append(r)

    return {
        "camera_id": cam.camera_id,
        "camera_name": cam.name,
        "camera_zone_id": str(cam.zone_id) if cam.zone_id else None,
        "frame": {"width": frame_w, "height": frame_h},
        "view": view,
        "selection": selection,
        "requested_zone_ids": selected_zone_ids,
        "available_zone_ids": available_zone_ids,
        "zone_count": len(zones),
        "zones": [
            _serialize_zone_for_camera(z, frame_w, frame_h, rules_by_zone.get(z.zone_id, []))
            for z in zones
        ],
    }


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
