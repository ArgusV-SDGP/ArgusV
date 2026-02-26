import os
import sys
import json
import random
import asyncio

sys.path.insert(0, '/app')

from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from shared.kafka_schemas import VlmRequest, VlmResult

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUME_TOPIC = "vlm-requests"
PRODUCE_TOPIC = "vlm-results"
CONSUMER_GROUP = "vlm-inference-group"

# Objects that raise the threat level in the stub
HIGH_RISK_OBJECTS = {"weapon", "knife", "gun"}
MEDIUM_RISK_OBJECTS = {"backpack", "luggage", "bag"}


def stub_infer(request: VlmRequest) -> VlmResult:
    """
    Stub inference — no real VLM call yet.
    Picks threat level based on detected objects so the pipeline produces
    interesting variety while we wire up the real OpenAI Vision call later.
    """
    objects = set(request.detection_context.detected_objects)

    if objects & HIGH_RISK_OBJECTS:
        threat_level = "HIGH"
        summary = f"High-risk object detected: {objects & HIGH_RISK_OBJECTS}"
        recommended_action = "dispatch_security"
        confidence = round(random.uniform(0.85, 0.99), 2)
    elif objects & MEDIUM_RISK_OBJECTS:
        threat_level = "MEDIUM"
        summary = f"Unattended item detected: {objects & MEDIUM_RISK_OBJECTS}"
        recommended_action = "alert_operator"
        confidence = round(random.uniform(0.65, 0.85), 2)
    else:
        threat_level = "LOW"
        summary = f"Routine activity: {list(objects)}"
        recommended_action = "log_only"
        confidence = round(random.uniform(0.50, 0.70), 2)

    return VlmResult(
        event_id=request.event_id,
        threat_level=threat_level,
        summary=summary,
        confidence=confidence,
        recommended_action=recommended_action,
    )


async def wait_for_kafka(bootstrap_servers: str, retries: int = 10, delay: float = 3.0):
    for attempt in range(retries):
        try:
            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            await probe.start()
            await probe.stop()
            return True
        except KafkaConnectionError as e:
            print(f"[vlm-inference] Kafka not ready (attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(delay)
    return False


async def run_inference_loop():
    ready = await wait_for_kafka(KAFKA_BOOTSTRAP_SERVERS)
    if not ready:
        print("[vlm-inference] Could not connect to Kafka. Exiting.")
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
    print(f"[vlm-inference] Consuming '{CONSUME_TOPIC}' → producing '{PRODUCE_TOPIC}'")

    try:
        async for msg in consumer:
            try:
                vlm_req = VlmRequest(**json.loads(msg.value.decode("utf-8")))
                print(
                    f"[vlm-inference] Processing event_id={vlm_req.event_id} "
                    f"camera={vlm_req.camera_id} objects={vlm_req.detection_context.detected_objects}"
                )

                result = stub_infer(vlm_req)

                await producer.send(
                    PRODUCE_TOPIC,
                    key=vlm_req.camera_id.encode("utf-8"),
                    value=result.model_dump_json().encode("utf-8"),
                )
                print(
                    f"[vlm-inference] Result event_id={result.event_id} "
                    f"threat={result.threat_level} confidence={result.confidence} "
                    f"→ '{result.recommended_action}'"
                )

            except Exception as e:
                print(f"[vlm-inference] Error: {e} | raw={msg.value}")
    finally:
        await consumer.stop()
        await producer.stop()
        print("[vlm-inference] Stopped.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_inference_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="vlm-inference", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "vlm-inference"}
