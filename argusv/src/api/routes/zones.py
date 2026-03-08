"""
api/routes/zones.py — Zone CRUD
Tasks: ZONE-01, ZONE-02, ZONE-03, ZONE-07, ZONE-08
"""

from datetime import datetime, timezone
import json
import logging
import uuid

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any, Optional

import config as cfg
from auth.jwt_handler import get_current_user, require_role, Role
from db.connection import get_db
from db.models import Zone

router = APIRouter(prefix="/api/zones", tags=["zones"], dependencies=[Depends(get_current_user)])
logger = logging.getLogger("api.zones")


class ZoneCreate(BaseModel):
    name: str
    polygon_coords: list[list[float]]        # [[x,y], ...] normalised 0..1
    zone_type: str = "security"
    dwell_threshold_sec: int = 30


def _parse_zone(zone: Zone) -> dict[str, Any]:
    return {
        "zone_id": str(zone.zone_id),
        "name": zone.name,
        "polygon_coords": zone.polygon_coords,
        "zone_type": zone.zone_type,
        "dwell_threshold_sec": zone.dwell_threshold_sec,
        "active": zone.active,
        "created_at": zone.created_at.isoformat() if zone.created_at else None,
    }


def _validate_polygon(points: list[list[float]]) -> None:
    if len(points) < 3:
        raise HTTPException(400, "polygon_coords must have at least 3 points")
    for i, pt in enumerate(points):
        if len(pt) != 2:
            raise HTTPException(400, f"polygon point at index {i} must have 2 numbers")
        x, y = pt
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise HTTPException(400, f"polygon point at index {i} must be normalized in [0,1]")


def _publish_zone_update(zone_id: str, action: str) -> None:
    payload = {
        "type": "ZONE_UPDATE",
        "action": action,
        "zone_id": zone_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = redis.from_url(cfg.REDIS_URL, decode_responses=True)
        raw = json.dumps(payload)
        r.set(f"config:zone:{zone_id}", raw)
        r.publish("config-updates", raw)
    except Exception as e:
        logger.warning(f"Failed to publish zone update for {zone_id}: {e}")


@router.get("/")
def list_zones(active_only: bool = Query(False), db: Session = Depends(get_db)):
    q = db.query(Zone)
    if active_only:
        q = q.filter(Zone.active == True)
    zones = q.order_by(Zone.created_at.desc()).all()
    return [_parse_zone(z) for z in zones]


@router.post("/", status_code=201, dependencies=[Depends(require_role(Role.ADMIN))])
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)):
    _validate_polygon(payload.polygon_coords)
    zone = Zone(
        zone_id=uuid.uuid4(),
        name=payload.name,
        polygon_coords=payload.polygon_coords,
        zone_type=payload.zone_type,
        dwell_threshold_sec=payload.dwell_threshold_sec,
        active=True,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    _publish_zone_update(str(zone.zone_id), "created")
    return _parse_zone(zone)


@router.put("/{zone_id}", dependencies=[Depends(require_role(Role.ADMIN))])
def update_zone(zone_id: str, payload: ZoneCreate, db: Session = Depends(get_db)):
    _validate_polygon(payload.polygon_coords)
    try:
        zid = uuid.UUID(zone_id)
    except ValueError:
        raise HTTPException(400, "Invalid zone_id")

    zone = db.query(Zone).filter(Zone.zone_id == zid).first()
    if not zone:
        raise HTTPException(404, "Zone not found")

    zone.name = payload.name
    zone.polygon_coords = payload.polygon_coords
    zone.zone_type = payload.zone_type
    zone.dwell_threshold_sec = payload.dwell_threshold_sec
    db.commit()
    db.refresh(zone)
    _publish_zone_update(str(zone.zone_id), "updated")
    return _parse_zone(zone)


@router.delete("/{zone_id}", status_code=204, dependencies=[Depends(require_role(Role.ADMIN))])
def delete_zone(zone_id: str, db: Session = Depends(get_db)):
    try:
        zid = uuid.UUID(zone_id)
    except ValueError:
        raise HTTPException(400, "Invalid zone_id")

    zone = db.query(Zone).filter(Zone.zone_id == zid).first()
    if not zone:
        raise HTTPException(404, "Zone not found")

    db.delete(zone)
    db.commit()
    _publish_zone_update(zone_id, "deleted")
