import os
from fastapi import FastAPI
from contextlib import asynccontextmanager

service_name = os.getenv("SERVICE_NAME", "Argus Service")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: logic (e.g., connect to Kafka)
    print(f"Starting up {service_name}...")
    yield
    # Shutdown: logic
    print(f"Shutting down {service_name}...")

app = FastAPI(title=service_name, lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": service_name}

@app.get("/")
def root():
    return {"message": f"Welcome to {service_name}"}
