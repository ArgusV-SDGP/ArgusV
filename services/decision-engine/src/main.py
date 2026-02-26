import os
import sys
import json
import uuid
import asyncio

sys.path.insert(0, '/app')

from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session
from typing import List
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError

from .schemas import ZoneCreate, ZoneResponse, RagConfigUpdate, NotificationRuleCreate
from .models import Zone, NotificationRule, RagConfig
from .config_manager import get_db, ConfigManager
from shared.kafka_schemas import VlmResult, Action

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUME_TOPIC = "vlm-results"
PRODUCE_TOPIC = "actions"
CONSUMER_GROUP = "decision-engine-group"

# Threat levels that trigger an action
ACTIONABLE_THREATS = {"HIGH", "CRITICAL"}


async def wait_for_kafka(bootstrap_servers: str, retries: int = 10, delay: float = 3.0):
    for attempt in range(retries):
        try:
            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            await probe.start()
            await probe.stop()
            return True
        except KafkaConnectionError as e:
            print(f"[decision-engine] Kafka not ready (attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(delay)
    return False


async def run_decision_loop():
    ready = await wait_for_kafka(KAFKA_BOOTSTRAP_SERVERS)
    if not ready:
        print("[decision-engine] Could not connect to Kafka. Exiting consumer.")
        return

    consumer = AIOKafkaConsumer(
        CONSUME_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

    await consumer.start()
    await producer.start()
    print(f"[decision-engine] Consuming '{CONSUME_TOPIC}' → producing '{PRODUCE_TOPIC}'")

    try:
        async for msg in consumer:
            try:
                result = VlmResult(**json.loads(msg.value.decode("utf-8")))
                print(
                    f"[decision-engine] Received event_id={result.event_id} "
                    f"threat={result.threat_level} action='{result.recommended_action}'"
                )

                if result.threat_level in ACTIONABLE_THREATS:
                    action = Action(
                        action_id=str(uuid.uuid4()),
                        event_id=result.event_id,
                        action_type="alert",
                        target="#security-alerts",
                        payload={
                            "threat_level": result.threat_level,
                            "summary": result.summary,
                            "confidence": result.confidence,
                            "recommended_action": result.recommended_action,
                        },
                    )
                    await producer.send(
                        PRODUCE_TOPIC,
                        key=result.event_id.encode("utf-8"),
                        value=action.model_dump_json().encode("utf-8"),
                    )
                    print(
                        f"[decision-engine] Action dispatched action_id={action.action_id} "
                        f"threat={result.threat_level} target={action.target}"
                    )
                else:
                    print(f"[decision-engine] threat={result.threat_level} — no action required")

            except Exception as e:
                print(f"[decision-engine] Error processing message: {e} | raw={msg.value}")
    finally:
        await consumer.stop()
        await producer.stop()
        print("[decision-engine] Stopped.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_decision_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="ArgusV Decision Engine", lifespan=lifespan)


# --- ZONE CONFIGURATION ---

@app.post("/api/zones", response_model=ZoneResponse)
def create_zone(zone: ZoneCreate, db: Session = Depends(get_db)):
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

    ConfigManager.sync_rule_to_redis(config.key, {"value": config.value})
    return {"status": "Config updated", "key": config.key}
