"""
api/routes/incidents.py — Incident management
Tasks: API-16, API-17

BRAYAN NOTE: Two endpoints exist:
  - GET  /{incident_id}  → returns serialised incident (id, camera, zone, threat, status, timestamps)
  - PATCH /{incident_id} → update status (OPEN/RESOLVED) and add annotation to metadata_json
Serializer: _serialize_incident() flattens the ORM model to a JSON-friendly dict.
No list endpoint here — the bulk GET /api/incidents lives in api/server.py.
No auth middleware applied yet — get_current_user is a no-op stub.
"""

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from auth.jwt_handler import get_current_user
from db.connection import get_db
from db.models import Incident

router = APIRouter(prefix="/api/incidents", tags=["incidents"], dependencies=[Depends(get_current_user)])


class IncidentPatch(BaseModel):
    status: Optional[str] = None          # OPEN | RESOLVED
    annotation: Optional[str] = None


def _serialize_incident(inc: Incident) -> dict:
    return {
        "incident_id": str(inc.incident_id),
        "camera_id": inc.camera_id,
        "zone_id": str(inc.zone_id) if inc.zone_id else None,
        "zone_name": inc.zone_name,
        "object_class": inc.object_class,
        "threat_level": inc.threat_level,
        "summary": inc.summary,
        "status": inc.status,
        "detected_at": inc.detected_at.isoformat() if inc.detected_at else None,
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
        "metadata_json": inc.metadata_json or {},
    }


@router.get("/{incident_id}")
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    try:
        iid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Invalid incident_id")
    inc = db.query(Incident).filter(Incident.incident_id == iid).first()
    if not inc:
        raise HTTPException(404, "Incident not found")
    return _serialize_incident(inc)


@router.patch("/{incident_id}")
def patch_incident(incident_id: str, payload: IncidentPatch, db: Session = Depends(get_db)):
    try:
        iid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(400, "Invalid incident_id")
    inc = db.query(Incident).filter(Incident.incident_id == iid).first()
    if not inc:
        raise HTTPException(404, "Incident not found")

    if payload.status is not None:
        status = payload.status.upper()
        if status not in {"OPEN", "RESOLVED"}:
            raise HTTPException(400, "status must be OPEN or RESOLVED")
        inc.status = status
        if status == "RESOLVED":
            inc.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        elif status == "OPEN":
            inc.resolved_at = None

    if payload.annotation is not None:
        meta = dict(inc.metadata_json or {})
        meta["annotation"] = payload.annotation
        meta["annotated_at"] = datetime.now(timezone.utc).isoformat()
        inc.metadata_json = meta

    db.commit()
    db.refresh(inc)
    return _serialize_incident(inc)
