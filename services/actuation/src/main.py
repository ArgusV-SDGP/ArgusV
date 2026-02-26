import os
import sys
import json
import asyncio
import signal

sys.path.insert(0, '/app')

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError
from shared.kafka_schemas import Action

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
MQTT_BROKER = os.getenv("MQTT_BROKER", "mosquitto")
CONSUME_TOPIC = "actions"
CONSUMER_GROUP = "actuation-group"


async def wait_for_kafka(bootstrap_servers: str, retries: int = 10, delay: float = 3.0):
    for attempt in range(retries):
        try:
            probe = AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
            await probe.start()
            await probe.stop()
            return True
        except KafkaConnectionError as e:
            print(f"[actuation] Kafka not ready (attempt {attempt + 1}/{retries}): {e}")
            await asyncio.sleep(delay)
    return False


async def run_actuation_loop():
    ready = await wait_for_kafka(KAFKA_BOOTSTRAP_SERVERS)
    if not ready:
        print("[actuation] Could not connect to Kafka. Exiting.")
        return

    consumer = AIOKafkaConsumer(
        CONSUME_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=CONSUMER_GROUP,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )

    await consumer.start()
    print(f"[actuation] Consuming '{CONSUME_TOPIC}'")

    try:
        async for msg in consumer:
            try:
                action = Action(**json.loads(msg.value.decode("utf-8")))

                if action.action_type == "actuate":
                    print(
                        f"[actuation] ACTUATE | action_id={action.action_id} "
                        f"target={action.target} payload={action.payload}"
                    )
                    # TODO: publish MQTT command to mosquitto
                    # mqtt_client.publish(f"argus/{action.target}", json.dumps(action.payload))
                else:
                    print(
                        f"[actuation] Skipping action_type='{action.action_type}' "
                        f"(not an actuation command)"
                    )

            except Exception as e:
                print(f"[actuation] Error processing message: {e} | raw={msg.value}")
    finally:
        await consumer.stop()
        print("[actuation] Stopped.")


async def main():
    loop = asyncio.get_running_loop()

    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)

    task = asyncio.create_task(run_actuation_loop())

    await stop.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("[actuation] Exited.")


if __name__ == "__main__":
    asyncio.run(main())
