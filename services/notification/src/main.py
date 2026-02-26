import os
import sys
import json
import asyncio

sys.path.insert(0, '/app')

from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from shared.kafka_schemas import Action

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUME_TOPIC = "actions"
CONSUMER_GROUP = "notification-group"


async def wait_for_kafka(bootstrap_servers: str, retries: int = 10, delay: float = 3.0):
    for attempt in range(retries):
        try:
            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            await probe.start()
            await probe.stop()
            return True
        except KafkaConnectionError as e:
            print(f"[notification] Kafka not ready (attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(delay)
    return False


async def run_notification_loop():
    ready = await wait_for_kafka(KAFKA_BOOTSTRAP_SERVERS)
    if not ready:
        print("[notification] Could not connect to Kafka. Exiting.")
        return

    consumer = AIOKafkaConsumer(
        CONSUME_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )

    await consumer.start()
    print(f"[notification] Consuming '{CONSUME_TOPIC}'")

    try:
        async for msg in consumer:
            try:
                action = Action(**json.loads(msg.value.decode("utf-8")))

                if action.action_type == "alert":
                    threat = action.payload.get("threat_level", "UNKNOWN")
                    summary = action.payload.get("summary", "")
                    print(
                        f"[notification] ALERT | action_id={action.action_id} "
                        f"event_id={action.event_id} threat={threat} "
                        f"target={action.target} | {summary}"
                    )
                    # TODO: send Slack message via slack_sdk
                    # TODO: send SMS via twilio

            except Exception as e:
                print(f"[notification] Error processing message: {e} | raw={msg.value}")
    finally:
        await consumer.stop()
        print("[notification] Stopped.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(run_notification_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="notification", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "notification"}
