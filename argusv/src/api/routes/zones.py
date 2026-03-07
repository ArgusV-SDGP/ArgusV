"""
api/routes/zones.py — Zone CRUD
Tasks: ZONE-01, ZONE-02, ZONE-03, ZONE-07, ZONE-08
"""
# TODO ZONE-01: implement GET /api/zones
# TODO ZONE-01: implement POST /api/zones
# TODO ZONE-10: implement PUT /api/zones/{id}
# TODO ZONE-11: implement DELETE /api/zones/{id}
# TODO ZONE-02: push to Redis on save → redis.set("config:zone:{id}", ...) + pubsub
# TODO ZONE-03: validate polygon ≥3 points, coords 0..1

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from db.connection import get_db
from db.models import Zone
import uuid, json

router = APIRouter(prefix="/api/zones", tags=["zones"])


class ZoneCreate(BaseModel):
    name: str
    polygon_coords: list        # [[x,y], ...] normalised 0..1
    zone_type: str = "security"
    dwell_threshold_sec: int = 30


@router.get("/")
def list_zones(db: Session = Depends(get_db)):
    # TODO ZONE-01
    return []


@router.post("/", status_code=201)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)):
    # TODO ZONE-01, ZONE-02, ZONE-03
    raise HTTPException(501, "Not implemented yet — see ZONE-01")


@router.put("/{zone_id}")
def update_zone(zone_id: str, payload: ZoneCreate, db: Session = Depends(get_db)):
    # TODO API-10
    raise HTTPException(501, "Not implemented yet — see API-10")


@router.delete("/{zone_id}", status_code=204)
def delete_zone(zone_id: str, db: Session = Depends(get_db)):
    # TODO API-11
    raise HTTPException(501, "Not implemented yet — see API-11")
