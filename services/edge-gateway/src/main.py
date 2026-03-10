import json
import logging
import os
import time
import uuid

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("edge-gateway")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
RAW_DETECTIONS_TOPIC = "raw-detections"
CAMERA_ID = os.getenv("CAMERA_ID", "cam-001")
DETECTION_INTERVAL_SEC = float(os.getenv("DETECTION_INTERVAL_SEC", "5"))
MAX_RETRY_DELAY_SEC = 30


def delivery_report(err, msg):
    if err is not None:
        logger.error("Message delivery failed: %s", err)
    else:
        logger.info(
            "Delivered to %s [partition %d] @ offset %d",
            msg.topic(),
            msg.partition(),
            msg.offset(),
        )


def ensure_topic(bootstrap_servers: str, topic: str) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    existing = admin.list_topics(timeout=10).topics
    if topic not in existing:
        futures = admin.create_topics(
            [NewTopic(topic, num_partitions=1, replication_factor=1)]
        )
        for t, f in futures.items():
            try:
                f.result()
                logger.info("Created topic: %s", t)
            except Exception as exc:
                logger.warning("Could not create topic %s: %s", t, exc)


def create_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "acks": "all",
        }
    )


def build_detection_event(camera_id: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "START",
        "camera_id": camera_id,
        "timestamp": time.time(),
        "object_class": "person",
        "confidence": 0.92,
        "track_id": 1,
        "zone_id": "zone-001",
        "zone_name": "Main Entrance",
        "dwell_sec": 0.0,
        "bbox": {"x1": 100, "y1": 150, "x2": 200, "y2": 350},
        "trigger_frame_b64": None,
        "status": "analyzing",
    }


def main() -> None:
    logger.info("Starting Edge Gateway...")

    producer = None
    retries = 0
    while producer is None:
        try:
            producer = create_producer()
            ensure_topic(KAFKA_BOOTSTRAP_SERVERS, RAW_DETECTIONS_TOPIC)
            logger.info("Connected to Kafka broker at %s", KAFKA_BOOTSTRAP_SERVERS)
        except Exception as exc:
            retries += 1
            wait = min(2**retries, MAX_RETRY_DELAY_SEC)
            logger.warning("Kafka not ready (%s), retrying in %ds...", exc, wait)
            time.sleep(wait)
            producer = None

    logger.info(
        "Edge Gateway running. Producing to '%s' every %.1fs.",
        RAW_DETECTIONS_TOPIC,
        DETECTION_INTERVAL_SEC,
    )

    while True:
        event = build_detection_event(CAMERA_ID)
        try:
            producer.produce(
                RAW_DETECTIONS_TOPIC,
                key=CAMERA_ID.encode(),
                value=json.dumps(event).encode(),
                callback=delivery_report,
            )
        except BufferError:
            logger.warning(
                "Producer queue full; flushing before retry for event %s",
                event["event_id"],
            )
            producer.flush()
            producer.produce(
                RAW_DETECTIONS_TOPIC,
                key=CAMERA_ID.encode(),
                value=json.dumps(event).encode(),
                callback=delivery_report,
            )
        producer.poll(0)
        logger.info("Produced detection event: %s", event["event_id"])
        time.sleep(DETECTION_INTERVAL_SEC)


if __name__ == "__main__":
    main()
