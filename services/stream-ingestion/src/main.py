import os
import json
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from aiokafka import AIOKafkaConsumer

service_name = os.getenv("SERVICE_NAME", "Argus Stream Ingestion")
# Pull the broker URL from the environment (default to localhost for local dev)
KAFKA_BROKER = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

async def consume_raw_detections():
    """Background task to consume detection events from the Edge Gateway."""
    consumer = AIOKafkaConsumer(
        'raw-detections', # The input topic
        bootstrap_servers=KAFKA_BROKER,
        group_id="stream-ingestion-group",
        auto_offset_reset='latest' # Only care about new events
    )
    
    await consumer.start()
    print(f"🎧 Started listening to 'raw-detections' on {KAFKA_BROKER}")
    
    try:
        async for msg in consumer:
            payload = json.loads(msg.value.decode('utf-8'))
            print(f"🔥 INCOMING DETECTION: [{payload['label']}] in {payload['zone_id']} (Event: {payload['event_id']})")
            
            # TODO: Phase 2 implementation
            # 1. Check Redis to debounce
            # 2. Extract Frames using OpenCV
            # 3. Upload to MinIO
            
    finally:
        await consumer.stop()

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 Starting up {service_name}...")
    
    # Spin up the Kafka consumer in the background
    consumer_task = asyncio.create_task(consume_raw_detections())
    
    yield
    
    print(f"🛑 Shutting down {service_name}...")
    # Cleanly cancel the background task on shutdown
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title=service_name, lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": service_name}

@app.get("/")
def root():
    return {"message": f"Welcome to {service_name}"}