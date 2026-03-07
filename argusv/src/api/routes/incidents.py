"""
api/routes/incidents.py — Incident management
Tasks: API-16, API-17
"""
# TODO API-16: PATCH /api/incidents/{id} — resolve / annotate
# TODO API-17: GET /api/incidents/{id}  — single incident detail

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from db.connection import get_db
from db.models import Incident
import uuid

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


class IncidentPatch(BaseModel):
    status: Optional[str] = None          # OPEN | RESOLVED
    annotation: Optional[str] = None


@router.get("/{incident_id}")
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    # TODO API-17
    raise HTTPException(501, "Not implemented yet — see API-17")


@router.patch("/{incident_id}")
def patch_incident(incident_id: str, payload: IncidentPatch, db: Session = Depends(get_db)):
    # TODO API-16
    raise HTTPException(501, "Not implemented yet — see API-16")
