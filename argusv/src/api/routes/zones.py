"""
api/routes/zones.py — Zone CRUD + polygon validation + Redis hot-reload.
Tasks: ZONE-01, ZONE-02, ZONE-03, ZONE-07, ZONE-08
"""

from datetime import datetime, timezone
import json
import logging
import uuid
from typing import Any, Optional

import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from shapely.geometry import Polygon
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from db.connection import get_db
from db.models import Zone

router = APIRouter(prefix="/api/zones", tags=["zones"])
logger = logging.getLogger("api.zones")


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1)
    polygon_coords: list[list[float]]
    zone_type: str = "security"
    dwell_threshold_sec: int = Field(default=30, ge=1)
    active: bool = True


class ZonePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    polygon_coords: Optional[list[list[float]]] = None
    zone_type: Optional[str] = None
    dwell_threshold_sec: Optional[int] = Field(default=None, ge=1)
    active: Optional[bool] = None


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


def _parse_zone_id(zone_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(zone_id)
    except ValueError:
        raise HTTPException(400, "Invalid zone_id")


def _validate_polygon(points: list[list[float]]) -> list[list[float]]:
    if len(points) < 3:
        raise HTTPException(400, "polygon_coords must have at least 3 points")

    normalized: list[list[float]] = []
    for i, pt in enumerate(points):
        if len(pt) != 2:
            raise HTTPException(400, f"polygon point at index {i} must contain [x, y]")
        try:
            x = float(pt[0])
            y = float(pt[1])
        except (TypeError, ValueError):
            raise HTTPException(400, f"polygon point at index {i} must be numeric")
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise HTTPException(400, f"polygon point at index {i} must be normalized in [0,1]")
        normalized.append([x, y])

    if normalized[0] == normalized[-1]:
        normalized = normalized[:-1]

    unique_points = {(x, y) for x, y in normalized}
    if len(normalized) < 3 or len(unique_points) < 3:
        raise HTTPException(400, "polygon_coords must contain at least 3 distinct points")

    polygon = Polygon(normalized)
    if polygon.area <= 0:
        raise HTTPException(400, "polygon_coords area must be greater than 0")
    if not polygon.is_valid:
        raise HTTPException(400, "polygon_coords must not self-intersect")

    return normalized


def _publish_zone_update(zone_id: str, action: str, zone_data: Optional[dict[str, Any]] = None) -> None:
    payload: dict[str, Any] = {
        "type": "ZONE_UPDATE",
        "action": action,
        "zone_id": zone_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if zone_data is not None:
        payload["zone"] = zone_data

    try:
        r = redis.from_url(cfg.REDIS_URL, decode_responses=True)
        if action in {"created", "updated"} and zone_data is not None:
            r.set(f"config:zone:{zone_id}", json.dumps(zone_data))
        if action == "deleted":
            r.delete(f"config:zone:{zone_id}")
        r.incr("config:zones:version")
        r.publish("config-updates", json.dumps(payload))
    except Exception as e:
        logger.warning(f"Failed to publish zone update for {zone_id}: {e}")


@router.get("")
def list_zones(
    active_only: bool = Query(False),
    zone_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    query = db.query(Zone)
    if active_only:
        query = query.filter(Zone.active == True)
    if zone_type:
        query = query.filter(Zone.zone_type == zone_type)
    zones = query.order_by(Zone.created_at.desc()).all()
    return [_parse_zone(z) for z in zones]


@router.get("/{zone_id}")
def get_zone(
    zone_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)),
):
    zid = _parse_zone_id(zone_id)
    zone = db.query(Zone).filter(Zone.zone_id == zid).first()
    if not zone:
        raise HTTPException(404, "Zone not found")
    return _parse_zone(zone)


@router.post("", status_code=201)
def create_zone(
    payload: ZoneCreate,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    polygon_coords = _validate_polygon(payload.polygon_coords)
    zone = Zone(
        zone_id=uuid.uuid4(),
        name=payload.name.strip(),
        polygon_coords=polygon_coords,
        zone_type=payload.zone_type,
        dwell_threshold_sec=payload.dwell_threshold_sec,
        active=payload.active,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    zone_data = _parse_zone(zone)
    _publish_zone_update(str(zone.zone_id), "created", zone_data)
    return zone_data


@router.put("/{zone_id}")
def update_zone(
    zone_id: str,
    payload: ZoneCreate,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    zid = _parse_zone_id(zone_id)
    zone = db.query(Zone).filter(Zone.zone_id == zid).first()
    if not zone:
        raise HTTPException(404, "Zone not found")

    zone.name = payload.name.strip()
    zone.polygon_coords = _validate_polygon(payload.polygon_coords)
    zone.zone_type = payload.zone_type
    zone.dwell_threshold_sec = payload.dwell_threshold_sec
    zone.active = payload.active
    db.commit()
    db.refresh(zone)
    zone_data = _parse_zone(zone)
    _publish_zone_update(str(zone.zone_id), "updated", zone_data)
    return zone_data


@router.patch("/{zone_id}")
def patch_zone(
    zone_id: str,
    payload: ZonePatch,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    zid = _parse_zone_id(zone_id)
    zone = db.query(Zone).filter(Zone.zone_id == zid).first()
    if not zone:
        raise HTTPException(404, "Zone not found")

    data = payload.dict(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        zone.name = data["name"].strip()
    if "polygon_coords" in data and data["polygon_coords"] is not None:
        zone.polygon_coords = _validate_polygon(data["polygon_coords"])
    if "zone_type" in data and data["zone_type"] is not None:
        zone.zone_type = data["zone_type"]
    if "dwell_threshold_sec" in data and data["dwell_threshold_sec"] is not None:
        zone.dwell_threshold_sec = data["dwell_threshold_sec"]
    if "active" in data and data["active"] is not None:
        zone.active = data["active"]

    db.commit()
    db.refresh(zone)
    zone_data = _parse_zone(zone)
    _publish_zone_update(str(zone.zone_id), "updated", zone_data)
    return zone_data


@router.delete("/{zone_id}", status_code=204)
def delete_zone(
    zone_id: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    zid = _parse_zone_id(zone_id)
    zone = db.query(Zone).filter(Zone.zone_id == zid).first()
    if not zone:
        raise HTTPException(404, "Zone not found")
    db.delete(zone)
    db.commit()
    _publish_zone_update(zone_id, "deleted")
