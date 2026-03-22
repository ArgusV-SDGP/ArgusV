"""
workers/actuation_worker.py — MQTT Device Actuation
----------------------------------------------------
Tasks: PIPE-05, NOTIF-05, NOTIF-06

Consumes bus.actions and actuates physical devices via MQTT:
- HIGH threats → trigger siren/alarm relay
- MEDIUM/HIGH threats → pan PTZ camera to detection bbox
- Incident-specific actions → custom MQTT payloads
"""

import asyncio
import json
import logging
from typing import Optional

import config as cfg
from bus import bus

logger = logging.getLogger("actuation-worker")

# Optional MQTT client - only imported if MQTT is enabled
_mqtt_client = None


async def actuation_worker():
    """
    Consumes bus.actions.
    On HIGH threat → MQTT publish to siren/relay topic.
    On MEDIUM/HIGH threat → MQTT pan camera toward detection bbox.

    Tasks: PIPE-05, NOTIF-05, NOTIF-06
    """
    global _mqtt_client

    if not cfg.MQTT_HOST:
        logger.info("[Actuation] MQTT not configured (MQTT_HOST not set) — worker running in dry-run mode")
    else:
        logger.info(f"[Actuation] Worker started — MQTT broker: {cfg.MQTT_HOST}:{cfg.MQTT_PORT}")
        # Initialize MQTT client if configured
        try:
            import aiomqtt
            _mqtt_client = aiomqtt.Client(
                hostname=cfg.MQTT_HOST,
                port=cfg.MQTT_PORT,
                username=cfg.MQTT_USER if cfg.MQTT_USER else None,
                password=cfg.MQTT_PASS if cfg.MQTT_PASS else None,
            )
            logger.info("[Actuation] MQTT client initialized")
        except ImportError:
            logger.warning("[Actuation] aiomqtt not installed — install with: pip install aiomqtt")
            _mqtt_client = None
        except Exception as e:
            logger.error(f"[Actuation] Failed to initialize MQTT client: {e}")
            _mqtt_client = None

    while True:
        action: dict = await bus.actions.get()
        try:
            threat_level = action.get("threat_level", "NONE")

            # High threat → trigger siren/alarm
            if threat_level == "HIGH":
                await _mqtt_trigger_siren(action)

            # Medium/High threat → PTZ to detection
            if threat_level in ("MEDIUM", "HIGH"):
                await _mqtt_ptz_to_detection(action)

            # Custom action payloads
            if action.get("action_type") == "custom_mqtt":
                await _mqtt_custom_action(action)

        except Exception as e:
            logger.error(f"[Actuation] Error processing action: {e}", exc_info=True)
        finally:
            bus.actions.task_done()


async def _mqtt_trigger_siren(action: dict):
    """
    Publish HIGH threat event to MQTT siren/relay topic.
    Task: NOTIF-05
    """
    topic = "argusv/siren/activate"
    payload = {
        "timestamp": action.get("timestamp"),
        "camera_id": action.get("camera_id"),
        "zone_name": action.get("zone_name"),
        "threat_level": action.get("threat_level"),
        "object_class": action.get("object_class"),
        "summary": action.get("summary"),
        "duration_sec": 10,  # How long to activate siren
    }

    if _mqtt_client:
        try:
            async with _mqtt_client as client:
                await client.publish(topic, payload=json.dumps(payload), qos=1)
                logger.info(f"🚨 [Actuation] Triggered siren via MQTT: {topic}")
        except Exception as e:
            logger.error(f"[Actuation] MQTT publish failed: {e}")
    else:
        logger.info(f"🚨 [Actuation] DRY-RUN: Would trigger siren for zone '{action.get('zone_name')}' (MQTT not configured)")


async def _mqtt_ptz_to_detection(action: dict):
    """
    Command PTZ camera to pan/tilt toward detection bbox.
    Task: NOTIF-06
    """
    camera_id = action.get("camera_id")
    bbox = action.get("bbox")

    if not bbox:
        logger.debug("[Actuation] No bbox in action — skipping PTZ")
        return

    # Calculate center point of bbox (normalized 0-1 coords)
    center_x = (bbox.get("x1", 0) + bbox.get("x2", 0)) / 2
    center_y = (bbox.get("y1", 0) + bbox.get("y2", 0)) / 2

    topic = f"argusv/ptz/{camera_id}/goto"
    payload = {
        "timestamp": action.get("timestamp"),
        "target_x": center_x,
        "target_y": center_y,
        "zoom_level": 1.0,  # No zoom by default
        "preset": None,
    }

    if _mqtt_client:
        try:
            async with _mqtt_client as client:
                await client.publish(topic, payload=json.dumps(payload), qos=1)
                logger.info(f"📹 [Actuation] PTZ command sent: {camera_id} → ({center_x:.2f}, {center_y:.2f})")
        except Exception as e:
            logger.error(f"[Actuation] PTZ MQTT publish failed: {e}")
    else:
        logger.info(f"📹 [Actuation] DRY-RUN: Would pan PTZ camera {camera_id} to ({center_x:.2f}, {center_y:.2f})")


async def _mqtt_custom_action(action: dict):
    """
    Publish custom MQTT action payload.
    Used for integrations and custom automations.
    """
    topic = action.get("mqtt_topic", "argusv/actions/custom")
    payload = action.get("mqtt_payload", {})

    if _mqtt_client:
        try:
            async with _mqtt_client as client:
                await client.publish(topic, payload=json.dumps(payload), qos=1)
                logger.info(f"[Actuation] Custom MQTT action published: {topic}")
        except Exception as e:
            logger.error(f"[Actuation] Custom MQTT publish failed: {e}")
    else:
        logger.info(f"[Actuation] DRY-RUN: Would publish custom MQTT to {topic}")
