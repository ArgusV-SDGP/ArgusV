import os
import sys
import json
import uuid
import asyncio

sys.path.insert(0, '/app')

from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from shared.kafka_schemas import RawDetection, VlmRequest

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUME_TOPIC = "raw-detections"
PRODUCE_TOPIC = "vlm-requests"
CONSUMER_GROUP = "stream-ingestion-group"


async def wait_for_kafka(bootstrap_servers: str, retries: int = 10, delay: float = 3.0):
    for attempt in range(retries):
        try:
            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            await probe.start()
            await probe.stop()
            return True
        except KafkaConnectionError as e:
            print(f"[stream-ingestion] Kafka not ready (attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(delay)
    return False


async def consume_and_forward():
    ready = await wait_for_kafka(KAFKA_BOOTSTRAP_SERVERS)
    if not ready:
        print("[stream-ingestion] Could not connect to Kafka. Exiting consumer.")
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
    print(f"[stream-ingestion] Consuming '{CONSUME_TOPIC}' → producing '{PRODUCE_TOPIC}'")

    try:
        async for msg in consumer:
            try:
                detection = RawDetection(**json.loads(msg.value.decode("utf-8")))
                print(
                    f"[stream-ingestion] Received event_id={detection.event_id} "
                    f"camera={detection.camera_id} objects={detection.detected_objects}"
                )

                # Build VlmRequest. frame_urls is empty until MinIO frame upload is wired in.
                vlm_req = VlmRequest(
                    event_id=detection.event_id,
                    camera_id=detection.camera_id,
                    zone_id=detection.zone_id,
                    frame_urls=[],  # TODO: upload frames to MinIO and populate
                    detection_context=detection,
                )

                await producer.send(
                    PRODUCE_TOPIC,
                    key=detection.camera_id.encode("utf-8"),
                    value=vlm_req.model_dump_json().encode("utf-8"),
                )
                print(f"[stream-ingestion] Forwarded VlmRequest event_id={vlm_req.event_id}")

            except Exception as e:
                print(f"[stream-ingestion] Error processing message: {e} | raw={msg.value}")
    finally:
        await consumer.stop()
        await producer.stop()
        print("[stream-ingestion] Stopped.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(consume_and_forward())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="stream-ingestion", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "stream-ingestion"}
