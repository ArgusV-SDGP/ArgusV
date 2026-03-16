import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("vlm-inference")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
VLM_REQUESTS_TOPIC = "vlm-requests"
SECURITY_DECISIONS_TOPIC = "security-decisions"
CONSUMER_GROUP = "vlm-inference-group"


def analyse(request: dict) -> dict:
    """
    Stub vision-language model analysis.

    In production this calls the OpenAI Vision API (or a local VLM) with the
    frame URL / base64 image from *request* and returns a structured result.
    """
    object_class = request.get("object_class", "unknown")
    dwell_sec = request.get("detection_context", {}).get("dwell_sec", 0)

    is_threat = dwell_sec >= 10 or object_class in ("person",)

    if is_threat and dwell_sec >= 10:
        threat_level = "HIGH"
        recommended_action = "ALERT"
    elif is_threat:
        threat_level = "MEDIUM"
        recommended_action = "MONITOR"
    else:
        threat_level = "LOW"
        recommended_action = "IGNORE"

    return {
        "threat_level": threat_level,
        "is_threat": is_threat,
        "summary": (
            f"Detected {object_class} in zone '{request.get('zone_name', 'unknown')}'. "
            f"Dwell time: {dwell_sec:.1f}s."
        ),
        "recommended_action": recommended_action,
    }


async def consume_vlm_requests(
    consumer: AIOKafkaConsumer, producer: AIOKafkaProducer
) -> None:
    async for msg in consumer:
        try:
            request = json.loads(msg.value)
            logger.info(
                "Received VLM request: request_id=%s camera=%s",
                request.get("request_id"),
                request.get("camera_id"),
            )

            result = analyse(request)

            decision = {
                "decision_id": str(uuid.uuid4()),
                "event_id": request["event_id"],
                "request_id": request["request_id"],
                "camera_id": request["camera_id"],
                "zone_id": request.get("zone_id"),
                "zone_name": request.get("zone_name"),
                "object_class": request.get("object_class", "unknown"),
                "timestamp": request["timestamp"],
                "threat_level": result["threat_level"],
                "is_threat": result["is_threat"],
                "summary": result["summary"],
                "recommended_action": result["recommended_action"],
            }

            await producer.send(
                SECURITY_DECISIONS_TOPIC,
                key=request["camera_id"].encode(),
                value=json.dumps(decision).encode(),
            )
            logger.info(
                "Published security decision: %s (threat=%s, action=%s)",
                decision["decision_id"],
                decision["threat_level"],
                decision["recommended_action"],
            )
        except Exception as exc:
            logger.error("Error processing vlm-requests message: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = AIOKafkaConsumer(
        VLM_REQUESTS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

    await consumer.start()
    await producer.start()
    logger.info(
        "VLM Inference: consuming '%s', producing '%s'",
        VLM_REQUESTS_TOPIC,
        SECURITY_DECISIONS_TOPIC,
    )

    task = asyncio.create_task(consume_vlm_requests(consumer, producer))
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
        logger.info("VLM Inference: Kafka consumer and producer stopped.")


app = FastAPI(title="VLM Inference Service", lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "vlm-inference"}


@app.get("/")
def root():
    return {"message": "Welcome to VLM Inference Service"}
