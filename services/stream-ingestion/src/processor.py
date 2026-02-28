import os
from datetime import datetime
from uuid import uuid4

from redis.asyncio import Redis

from config_loader import StreamConfigLoader
from frame_extractor import extract_frames
from storage import upload_frame
from kafka_io import send_vlm_request

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INCIDENT_TTL_SECONDS = int(os.getenv("INCIDENT_TTL_SECONDS", "10"))

config_loader = StreamConfigLoader()
redis_client = Redis.from_url(REDIS_URL, decode_responses=True)


async def _get_or_create_incident(zone_id: str) -> tuple[str | None, bool]:
    """Return (incident_id, already_active)."""
    key = f"incident:active:{zone_id}"
    existing = await redis_client.get(key)
    if existing:
        await redis_client.expire(key, INCIDENT_TTL_SECONDS)
        return existing, True

    incident_id = f"inc_{uuid4().hex[:12]}"
    await redis_client.set(key, incident_id, ex=INCIDENT_TTL_SECONDS)
    return incident_id, False

async def process_detection(event: dict):
    zone_id = event["zone_id"]
    incident_id, already_active = await _get_or_create_incident(zone_id)
    if already_active:
        print(f"[Processor] Zone {zone_id} already active. Skipping.")
        return

    # 1. Load zone config
    zone_config = config_loader.get_zone_config(zone_id)
    if not zone_config or not zone_config.get("enabled", True):
        print(f"[Processor] Zone {zone_id} disabled. Skipping.")
        return

    # 2. Extract frames
    rtsp_url = event.get("rtsp_url") or zone_config.get("camera_rtsp")
    if not rtsp_url:
        print(f"[Processor] Missing rtsp_url for Zone {zone_id}. Skipping.")
        return

    frames = extract_frames(
        rtsp_url=rtsp_url,
        offsets=zone_config.get("frame_offsets", [0, 1, 2]),
    )

    # 3. Upload frames
    frame_urls = []
    for offset, frame_bytes in frames.items():
        object_name = f"{zone_id}/{event['event_id']}_t{offset}.jpg"
        url = upload_frame(frame_bytes, object_name)
        frame_urls.append({
            "t_offset": offset,
            "url": url
        })

    # 4. BUILD OUTPUT EVENT 
    output_event = {
        "event_id": event["event_id"],
        "incident_id": incident_id,
        "zone_id": zone_id,
        "camera_id": event.get("camera_id"),
        "rtsp_url": rtsp_url,
        "detection_type": event.get("detection_type")
        or (event.get("detections") or [{}])[0].get("label"),
        "frames": frame_urls,
        "metadata": {
            "confidence": event.get("confidence")
            or (event.get("detections") or [{}])[0].get("confidence"),
            "source": "stream-ingestion"
        },
        "created_at": datetime.utcnow().isoformat()
    }

    # 5. Publish to Kafka
    await send_vlm_request(output_event)
