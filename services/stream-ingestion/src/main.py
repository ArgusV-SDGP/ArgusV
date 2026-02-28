import os
import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from kafka_io import consume_detections, stop_producer
from processor import process_detection

service_name = os.getenv("SERVICE_NAME", "Argus Service")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: logic (e.g., connect to Kafka)
    print(f"Starting up {service_name}...")
    consumer_task = asyncio.create_task(consume_detections(process_detection))
    yield
    # Shutdown: logic
    consumer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer_task
    await stop_producer()
    print(f"Shutting down {service_name}...")

app = FastAPI(title=service_name, lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": service_name}

@app.get("/")
def root():
    return {"message": f"Welcome to {service_name}"}
