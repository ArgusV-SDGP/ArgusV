import time
import os

service_name = os.getenv("SERVICE_NAME", "Unknown Service")

print(f"Starting {service_name}...")

while True:
    print(f"{service_name} is running...")
    time.sleep(10)
