import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from .schemas import ZoneCreate, ZoneResponse, RagConfigUpdate, NotificationRuleCreate
from .models import Zone, NotificationRule, RagConfig
from .config_manager import get_db, ConfigManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("decision-engine")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
SECURITY_DECISIONS_TOPIC = "security-decisions"
ACTIONS_TOPIC = "actions"
CONSUMER_GROUP = "decision-engine-group"

ACTION_ALERT = "ALERT"
ACTION_LOG = "LOG"
ACTION_IGNORE = "IGNORE"


def decide_action(decision: dict) -> str:
    """Map a VLM decision to a concrete action type."""
    recommended = decision.get("recommended_action", "IGNORE")
    if recommended == "ALERT":
        return ACTION_ALERT
    if recommended == "MONITOR":
        return ACTION_LOG
    return ACTION_IGNORE


async def consume_security_decisions(
    consumer: AIOKafkaConsumer, producer: AIOKafkaProducer
) -> None:
    async for msg in consumer:
        try:
            decision = json.loads(msg.value)
            logger.info(
                "Received security decision: decision_id=%s threat=%s",
                decision.get("decision_id"),
                decision.get("threat_level"),
            )

            action_type = decide_action(decision)

            action = {
                "action_id": str(uuid.uuid4()),
                "event_id": decision.get("event_id"),
                "action_type": action_type,
                "camera_id": decision.get("camera_id"),
                "zone_name": decision.get("zone_name"),
                "object_class": decision.get("object_class", "unknown"),
                "threat_level": decision.get("threat_level", "UNKNOWN"),
                "summary": decision.get("summary"),
                "timestamp": time.time(),
            }

            await producer.send(
                ACTIONS_TOPIC,
                key=decision.get("camera_id", "unknown").encode(),
                value=json.dumps(action).encode(),
            )
            logger.info(
                "Published action: %s (type=%s)",
                action["action_id"],
                action["action_type"],
            )
        except Exception as exc:
            logger.error("Error processing security-decisions message: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = AIOKafkaConsumer(
        SECURITY_DECISIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

    await consumer.start()
    await producer.start()
    logger.info(
        "Decision Engine: consuming '%s', producing '%s'",
        SECURITY_DECISIONS_TOPIC,
        ACTIONS_TOPIC,
    )

    task = asyncio.create_task(consume_security_decisions(consumer, producer))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await consumer.stop()
        await producer.stop()
        logger.info("Decision Engine: Kafka consumer and producer stopped.")


app = FastAPI(title="ArgusV Decision Engine", lifespan=lifespan)

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
