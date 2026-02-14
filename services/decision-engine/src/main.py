from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
import uuid

from .schemas import ZoneCreate, ZoneResponse, RagConfigUpdate, NotificationRuleCreate
from .models import Zone, NotificationRule, RagConfig
from .config_manager import get_db, ConfigManager

app = FastAPI(title="ArgusV Decision Engine")

# --- ZONE CONFIGURATION ---

@app.post("/api/zones", response_model=ZoneResponse)
def create_zone(zone: ZoneCreate, db: Session = Depends(get_db)):
    # 1. Write to Postgres
    db_zone = Zone(
        name=zone.name,
        polygon_coords=zone.polygon_coords,
        zone_type=zone.zone_type,
        dwell_threshold_sec=zone.dwell_threshold_sec,
        is_active=zone.is_active
    )
    db.add(db_zone)
    db.commit()
    db.refresh(db_zone)
    
    # 2. Sync to Redis (The "Skeletal" Implementation)
    ConfigManager.sync_zone_to_redis(
        zone_id=str(db_zone.zone_id),
        zone_data={
            "id": str(db_zone.zone_id),
            "name": db_zone.name,
            "threshold": db_zone.dwell_threshold_sec,
            "active": db_zone.is_active
        }
    )
    
    return db_zone

@app.get("/api/zones", response_model=List[ZoneResponse])
def list_zones(db: Session = Depends(get_db)):
    return db.query(Zone).filter(Zone.is_active == True).all()

# --- NOTIFICATION RULES ---

@app.post("/api/rules")
def create_rule(rule: NotificationRuleCreate, db: Session = Depends(get_db)):
    db_rule = NotificationRule(**rule.model_dump())
    db.add(db_rule)
    db.commit()
    return {"status": "Rule created", "id": str(db_rule.id)}

# --- RAG CONFIG ---

@app.post("/api/rag/config")
def update_rag_config(config: RagConfigUpdate, db: Session = Depends(get_db)):
    db_config = db.query(RagConfig).filter(RagConfig.key == config.key).first()
    if not db_config:
        db_config = RagConfig(key=config.key)
    
    db_config.value = config.value
    db_config.group = config.group
    db.add(db_config)
    db.commit()
    
    # Sync to Redis for the RAG service to read
    ConfigManager.sync_rule_to_redis(config.key, {"value": config.value})
    
    return {"status": "Config updated", "key": config.key}
