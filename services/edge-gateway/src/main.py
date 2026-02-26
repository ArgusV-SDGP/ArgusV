import os
import sys
import time
import uuid
import signal
import random
from datetime import datetime, timezone

# Shared lib is mounted at /app/shared
sys.path.insert(0, '/app')

from confluent_kafka import Producer
from shared.kafka_schemas import RawDetection

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = "raw-detections"

# Simulated camera/zone IDs for smoke testing
CAMERAS = ["cam-01", "cam-02", "cam-03"]
ZONES = ["zone-entrance", "zone-parking", "zone-lobby"]
OBJECT_POOL = ["person", "backpack", "vehicle", "bicycle", "luggage"]

running = True


def on_delivery(err, msg):
    if err:
        print(f"[Producer] Delivery FAILED: {err}")
    else:
        print(
            f"[Producer] Delivered to {msg.topic()} "
            f"[partition={msg.partition()}, offset={msg.offset()}]"
        )


def build_detection(camera_id: str, zone_id: str) -> RawDetection:
    objects = random.sample(OBJECT_POOL, k=random.randint(1, 3))
    return RawDetection(
        event_id=str(uuid.uuid4()),
        camera_id=camera_id,
        zone_id=zone_id,
        detected_objects=objects,
        confidence_scores=[round(random.uniform(0.6, 0.99), 2) for _ in objects],
        frame_timestamp=datetime.now(timezone.utc),
    )


def main():
    global running

    def handle_shutdown(signum, frame):
        global running
        print("\n[Producer] Shutting down...")
        running = False

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})
    print(f"[Producer] Connected to Kafka at {KAFKA_BOOTSTRAP_SERVERS}")
    print(f"[Producer] Publishing to topic: {TOPIC}")

    while running:
        camera_id = random.choice(CAMERAS)
        zone_id = random.choice(ZONES)
        detection = build_detection(camera_id, zone_id)

        payload = detection.model_dump_json().encode("utf-8")

        producer.produce(
            topic=TOPIC,
            key=camera_id.encode("utf-8"),  # partition by camera
            value=payload,
            on_delivery=on_delivery,
        )
        producer.poll(0)  # trigger delivery callbacks without blocking

        print(f"[Producer] Sent event_id={detection.event_id} | camera={camera_id} | objects={detection.detected_objects}")
        time.sleep(5)

    producer.flush()
    print("[Producer] Flushed and exited.")


if __name__ == "__main__":
    main()
