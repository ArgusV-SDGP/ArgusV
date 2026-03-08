"""api/routes/configuration.py — Runtime configuration + rules CRUD."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional

import redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import get_current_user
from db.connection import get_db
from db.models import NotificationRule, RagConfig

router = APIRouter(tags=["configuration"], dependencies=[Depends(get_current_user)])
logger = logging.getLogger("api.configuration")


class RuntimeConfigPatch(BaseModel):
    detect_fps: Optional[int] = None
    conf_threshold: Optional[float] = None
    use_motion_gate: Optional[bool] = None
    motion_threshold: Optional[float] = None
    use_tracker: Optional[bool] = None
    loiter_threshold_sec: Optional[int] = None
    embed_frame: Optional[bool] = None
    frame_jpeg_q: Optional[int] = None
    recordings_enabled: Optional[bool] = None
    segment_duration_sec: Optional[int] = None
    recordings_retain_days: Optional[int] = None
    use_tiered_vlm: Optional[bool] = None
    vlm_model: Optional[str] = None
    vlm_triage_model: Optional[str] = None
    vlm_max_workers: Optional[int] = None
    rate_limit_ttl_sec: Optional[int] = None


class NotificationRuleCreate(BaseModel):
    zone_id: str = "global"
    severity: str
    channels: list[str]
    config: dict[str, Any] = {}


class RagConfigUpsert(BaseModel):
    value: Any
    group: str = "rag"


def _runtime_defaults() -> dict[str, Any]:
    return {
        "detect_fps": cfg.DETECT_FPS,
        "conf_threshold": cfg.CONF_THRESHOLD,
        "use_motion_gate": cfg.USE_MOTION_GATE,
        "motion_threshold": cfg.MOTION_THRESHOLD,
        "use_tracker": cfg.USE_TRACKER,
        "loiter_threshold_sec": cfg.LOITER_SEC,
        "embed_frame": cfg.EMBED_FRAME,
        "frame_jpeg_q": cfg.FRAME_JPEG_Q,
        "recordings_enabled": cfg.RECORDINGS_ENABLED,
        "segment_duration_sec": cfg.SEGMENT_DURATION_SEC,
        "recordings_retain_days": cfg.RECORDINGS_RETAIN_DAYS,
        "use_tiered_vlm": cfg.USE_TIERED_VLM,
        "vlm_model": cfg.VLM_MODEL,
        "vlm_triage_model": cfg.VLM_TRIAGE_MODEL,
        "vlm_max_workers": cfg.VLM_MAX_WORKERS,
        "rate_limit_ttl_sec": cfg.RATE_LIMIT_TTL,
    }


def _publish_config_update(update_type: str) -> None:
    payload = {
        "type": update_type,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = redis.from_url(cfg.REDIS_URL, decode_responses=True)
        r.publish("config-updates", json.dumps(payload))
    except Exception as e:
        logger.warning(f"Failed to publish config update ({update_type}): {e}")


@router.get("/api/config/runtime")
def get_runtime_config(db: Session = Depends(get_db)):
    current = _runtime_defaults()
    rows = db.query(RagConfig).filter(RagConfig.group == "runtime").all()
    for row in rows:
        try:
            current[row.key] = json.loads(row.value)
        except Exception:
            current[row.key] = row.value
    return current


@router.put("/api/config/runtime")
def update_runtime_config(payload: RuntimeConfigPatch, db: Session = Depends(get_db)):
    updates = payload.dict(exclude_unset=True)
    for key, value in updates.items():
        row = db.query(RagConfig).filter(RagConfig.group == "runtime", RagConfig.key == key).first()
        if not row:
            row = RagConfig(key=key, group="runtime", value=json.dumps(value))
            db.add(row)
        else:
            row.value = json.dumps(value)
    db.commit()
    _publish_config_update("RUNTIME_UPDATE")
    return {"updated": list(updates.keys()), "count": len(updates)}


@router.post("/api/config/apply")
def apply_config():
    _publish_config_update("CONFIG_APPLY")
    return {"status": "ok", "message": "config apply signal published"}


@router.get("/api/notification-rules")
def list_notification_rules(db: Session = Depends(get_db)):
    rows = db.query(NotificationRule).all()
    return [
        {
            "id": str(r.id),
            "zone_id": r.zone_id,
            "severity": r.severity,
            "channels": r.channels or [],
            "config": r.config or {},
        }
        for r in rows
    ]


@router.post("/api/notification-rules", status_code=201)
def create_notification_rule(payload: NotificationRuleCreate, db: Session = Depends(get_db)):
    row = NotificationRule(
        zone_id=payload.zone_id,
        severity=payload.severity.upper(),
        channels=payload.channels,
        config=payload.config,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    _publish_config_update("NOTIFICATION_RULE_UPDATE")
    return {
        "id": str(row.id),
        "zone_id": row.zone_id,
        "severity": row.severity,
        "channels": row.channels or [],
        "config": row.config or {},
    }


@router.put("/api/notification-rules/{rule_id}")
def update_notification_rule(rule_id: str, payload: NotificationRuleCreate, db: Session = Depends(get_db)):
    import uuid

    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(400, "Invalid rule_id")

    row = db.query(NotificationRule).filter(NotificationRule.id == rid).first()
    if not row:
        raise HTTPException(404, "Notification rule not found")

    row.zone_id = payload.zone_id
    row.severity = payload.severity.upper()
    row.channels = payload.channels
    row.config = payload.config
    db.commit()
    _publish_config_update("NOTIFICATION_RULE_UPDATE")
    return {
        "id": str(row.id),
        "zone_id": row.zone_id,
        "severity": row.severity,
        "channels": row.channels or [],
        "config": row.config or {},
    }


@router.delete("/api/notification-rules/{rule_id}", status_code=204)
def delete_notification_rule(rule_id: str, db: Session = Depends(get_db)):
    import uuid

    try:
        rid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(400, "Invalid rule_id")

    row = db.query(NotificationRule).filter(NotificationRule.id == rid).first()
    if not row:
        raise HTTPException(404, "Notification rule not found")

    db.delete(row)
    db.commit()
    _publish_config_update("NOTIFICATION_RULE_UPDATE")


@router.get("/api/rag-config")
def list_rag_config(db: Session = Depends(get_db), group: str = "rag"):
    rows = db.query(RagConfig).filter(RagConfig.group == group).all()
    out = []
    for row in rows:
        try:
            value = json.loads(row.value)
        except Exception:
            value = row.value
        out.append({"key": row.key, "group": row.group, "value": value})
    return out


@router.put("/api/rag-config/{key}")
def upsert_rag_config(key: str, payload: RagConfigUpsert, db: Session = Depends(get_db)):
    row = db.query(RagConfig).filter(RagConfig.key == key, RagConfig.group == payload.group).first()
    if not row:
        row = RagConfig(key=key, group=payload.group, value=json.dumps(payload.value))
        db.add(row)
    else:
        row.value = json.dumps(payload.value)
    db.commit()
    _publish_config_update("RAG_CONFIG_UPDATE")
    return {"key": key, "group": payload.group, "value": payload.value}


@router.delete("/api/rag-config/{key}", status_code=204)
def delete_rag_config(key: str, group: str = "rag", db: Session = Depends(get_db)):
    row = db.query(RagConfig).filter(RagConfig.key == key, RagConfig.group == group).first()
    if not row:
        raise HTTPException(404, "RagConfig key not found")
    db.delete(row)
    db.commit()
    _publish_config_update("RAG_CONFIG_UPDATE")
