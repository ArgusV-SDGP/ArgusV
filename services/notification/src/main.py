import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("notification")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
ACTIONS_TOPIC = "actions"
CONSUMER_GROUP = "notification-group"


async def dispatch_notification(action: dict) -> None:
    """
    Dispatch a notification for the given action.

    In production this sends Slack messages, SMS via Twilio, or emails based
    on the notification rules stored in the database.  For the basic flow we
    log the alert details so the end-to-end pipeline can be verified.
    """
    action_type = action.get("action_type", "IGNORE")
    camera_id = action.get("camera_id", "unknown")
    zone_name = action.get("zone_name", "unknown")
    threat_level = action.get("threat_level", "UNKNOWN")
    summary = action.get("summary", "")

    if action_type == "ALERT":
        logger.warning(
            "[NOTIFY] ALERT — camera=%s zone='%s' threat=%s | %s",
            camera_id,
            zone_name,
            threat_level,
            summary,
        )
    elif action_type == "LOG":
        logger.info(
            "[NOTIFY] LOG — camera=%s zone='%s' threat=%s",
            camera_id,
            zone_name,
            threat_level,
        )
    else:
        logger.debug("[NOTIFY] No notification required (action_type=%s)", action_type)


async def consume_actions(consumer: AIOKafkaConsumer) -> None:
    async for msg in consumer:
        try:
            action = json.loads(msg.value)
            logger.info(
                "Received action: action_id=%s type=%s",
                action.get("action_id"),
                action.get("action_type"),
            )
            await dispatch_notification(action)
            await consumer.commit()
        except Exception as exc:
            logger.error("Error processing actions message: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = AIOKafkaConsumer(
        ACTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )

    await consumer.start()
    logger.info("Notification: consuming '%s'", ACTIONS_TOPIC)

    task = asyncio.create_task(consume_actions(consumer))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Task was cancelled as part of application shutdown; this is expected.
            logger.debug("Notification consumer task cancelled during shutdown.")
        await consumer.stop()
        logger.info("Notification: Kafka consumer stopped.")


app = FastAPI(title="Notification Service", lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "notification"}


@app.get("/")
def root():
    return {"message": "Welcome to Notification Service"}
