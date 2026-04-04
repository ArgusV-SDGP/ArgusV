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
logger = logging.getLogger("stream-ingestion")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
RAW_DETECTIONS_TOPIC = "raw-detections"
VLM_REQUESTS_TOPIC = "vlm-requests"
CONSUMER_GROUP = "stream-ingestion-group"


async def consume_raw_detections(
    consumer: AIOKafkaConsumer, producer: AIOKafkaProducer
) -> None:
    async for msg in consumer:
        try:
            event = json.loads(msg.value)
            logger.info(
                "Received detection: event_id=%s camera=%s",
                event.get("event_id"),
                event.get("camera_id"),
            )

            vlm_request = {
                "request_id": str(uuid.uuid4()),
                "event_id": event["event_id"],
                "camera_id": event["camera_id"],
                "zone_id": event.get("zone_id"),
                "zone_name": event.get("zone_name"),
                "object_class": event.get("object_class", "unknown"),
                "timestamp": event["timestamp"],
                # TODO: upload captured frame to MinIO and set a real presigned URL here.
                "frame_url": None,
                "detection_context": {
                    "confidence": event.get("confidence"),
                    "bbox": event.get("bbox"),
                    "dwell_sec": event.get("dwell_sec", 0),
                    "trigger_frame_b64": event.get("trigger_frame_b64"),
                },
            }

            await producer.send(
                VLM_REQUESTS_TOPIC,
                key=event["camera_id"].encode(),
                value=json.dumps(vlm_request).encode(),
            )
            logger.info("Forwarded VLM request: %s", vlm_request["request_id"])
            await consumer.commit()
        except Exception as exc:
            logger.error("Error processing raw-detections message: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = AIOKafkaConsumer(
        RAW_DETECTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

    consumer_started = False
    producer_started = False
    try:
        await consumer.start()
        consumer_started = True
        await producer.start()
        producer_started = True
    except Exception:
        logger.exception(
            "Failed to start Kafka consumer/producer, shutting down partially-started clients."
        )
        if consumer_started:
            await consumer.stop()
        if producer_started:
            await producer.stop()
        raise
    logger.info(
        "Stream Ingestion: consuming '%s', producing '%s'",
        RAW_DETECTIONS_TOPIC,
        VLM_REQUESTS_TOPIC,
    )

    task = asyncio.create_task(consume_raw_detections(consumer, producer))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Task was cancelled as part of application shutdown; this is expected.
            logger.debug("Background consumption task cancelled during shutdown.")
        await consumer.stop()
        await producer.stop()
        logger.info("Stream Ingestion: Kafka consumer and producer stopped.")


app = FastAPI(title="Stream Ingestion Service", lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "stream-ingestion"}


@app.get("/")
def root():
    return {"message": "Welcome to Stream Ingestion Service"}
