"""Kafka consumer/producer helpers for stream-ingestion."""

import json
import os
from typing import Awaitable, Callable

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
RAW_DETECTIONS_TOPIC = os.getenv("RAW_DETECTIONS_TOPIC", "raw-detections")
VLM_REQUESTS_TOPIC = os.getenv("VLM_REQUESTS_TOPIC", "vlm-requests")
CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "stream-ingestion")

_producer: AIOKafkaProducer | None = None


async def _get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await _producer.start()
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None


async def send_vlm_request(payload: dict) -> None:
    producer = await _get_producer()
    await producer.send_and_wait(VLM_REQUESTS_TOPIC, payload)


async def consume_detections(handler: Callable[[dict], Awaitable[None]]) -> None:
    consumer = AIOKafkaConsumer(
        RAW_DETECTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=CONSUMER_GROUP,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await consumer.start()
    try:
        async for message in consumer:
            await handler(message.value)
    finally:
        await consumer.stop()
