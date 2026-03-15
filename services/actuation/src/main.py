import asyncio
import json
import logging
import os

from aiokafka import AIOKafkaConsumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("actuation")

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
ACTIONS_TOPIC = "actions"
CONSUMER_GROUP = "actuation-group"
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MAX_RETRY_DELAY_SEC = 30


def actuate(action: dict) -> None:
    """
    Execute a physical actuation command based on the incoming action.

    In production this publishes MQTT commands to activate sirens, strobe
    lights, door locks, or PTZ presets via the Mosquitto broker.  For the
    basic flow we log the intent so the end-to-end pipeline can be verified.
    """
    action_type = action.get("action_type", "IGNORE")
    camera_id = action.get("camera_id", "unknown")
    zone_name = action.get("zone_name", "unknown")
    threat_level = action.get("threat_level", "UNKNOWN")

    if action_type == "ALERT":
        logger.warning(
            "[ACTUATE] ALERT — camera=%s zone='%s' threat=%s "
            "→ would publish MQTT alarm ON to %s:%d",
            camera_id,
            zone_name,
            threat_level,
            MQTT_BROKER,
            MQTT_PORT,
        )
        # Production: mqtt_client.publish(f"argus/{camera_id}/alarm", '{"state":"ON"}')
    elif action_type == "LOG":
        logger.info(
            "[ACTUATE] LOG — camera=%s zone='%s' (no physical action)",
            camera_id,
            zone_name,
        )
    else:
        logger.debug("[ACTUATE] No actuation required (action_type=%s)", action_type)


async def consume_actions() -> None:
    consumer = AIOKafkaConsumer(
        ACTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
    )

    await consumer.start()
    logger.info("Actuation: consuming '%s'", ACTIONS_TOPIC)
    try:
        async for msg in consumer:
            try:
                action = json.loads(msg.value)
                logger.info(
                    "Received action: action_id=%s type=%s",
                    action.get("action_id"),
                    action.get("action_type"),
                )
                actuate(action)
            except Exception as exc:
                logger.error("Error processing action message: %s", exc)
    finally:
        await consumer.stop()


async def main() -> None:
    logger.info("Starting Actuation Service...")
    retries = 0
    while True:
        try:
            await consume_actions()
        except Exception as exc:
            retries += 1
            wait = min(2**retries, MAX_RETRY_DELAY_SEC)
            logger.error("Consumer error: %s. Retrying in %ds...", exc, wait)
            await asyncio.sleep(wait)


if __name__ == "__main__":
    asyncio.run(main())
