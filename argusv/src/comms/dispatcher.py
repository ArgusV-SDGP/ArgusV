"""
comms/dispatcher.py — Internal event dispatcher
-------------------------------------------------
Frigate equivalent: frigate/comms/dispatcher.py

Handles dispatching events to:
  - MQTT (external devices)
  - WebSocket (dashboard)
  - WebPush (browser push notifications)
  - Internal bus queues

ArgusV uses asyncio.Queue as the primary bus,
but this module handles fan-out to external systems.
"""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger("comms.dispatcher")


class EventDispatcher:
    """
    Central dispatcher for all outbound events.
    Equivalent to Frigate's Dispatcher class.

    Outbound channels:
      mqtt      → actuation_worker (MQTT relay/siren)
      websocket → ws_handler fan-out
      webpush   → browser push notifications (TODO)
      webhook   → arbitrary HTTP endpoint (TODO)
    """

    def __init__(self, alerts_ws_queue: asyncio.Queue):
        self._ws_queue   = alerts_ws_queue
        self._mqtt_client: Optional[object] = None     # TODO NOTIF-05: aiomqtt client
        self._webpush_client: Optional[object] = None  # TODO: webpush client

    async def dispatch_detection(self, event: dict):
        """Route a raw detection to appropriate channels."""
        # Always push to WebSocket
        await self._ws_queue.put({"type": "fast_alert", **event})

    async def dispatch_alert(self, event: dict):
        """
        Route a VLM-enriched alert to:
        - WebSocket (always)
        - MQTT (if HIGH + MQTT configured)
        - WebPush (if subscribed)
        - Webhook (if configured)
        """
        threat = event.get("threat_level", "LOW")

        # WebSocket — always
        await self._ws_queue.put({"type": "vlm_update", **event})

        # MQTT — HIGH threats
        if threat == "HIGH":
            await self._dispatch_mqtt(event)
            await self._dispatch_webpush(event)

    async def _dispatch_mqtt(self, event: dict):
        """
        Publish alert to MQTT topic.
        Task: NOTIF-05
        """
        if self._mqtt_client:
            topic = f"argusv/{event.get('camera_id')}/alert"
            try:
                await self._mqtt_client.publish(topic, json.dumps(event))
                logger.info(f"[Dispatcher] MQTT published to {topic}")
            except Exception as e:
                logger.error(f"[Dispatcher] MQTT publish failed: {e}")
        else:
            logger.debug(f"[Dispatcher] MQTT not configured for {event.get('camera_id')}")

    async def _dispatch_webpush(self, event: dict):
        """
        Send browser push notification for HIGH threats.
        Task: NOTIF-07 (WebPush)
        """
        if self._webpush_client:
            try:
                await self._webpush_client.send_notification(event)
                logger.info(f"[Dispatcher] WebPush sent for {event.get('incident_id')}")
            except Exception as e:
                logger.error(f"[Dispatcher] WebPush failed: {e}")
        else:
            logger.debug(f"[Dispatcher] WebPush not configured")

    async def dispatch_webhook(self, event: dict, webhook_url: str):
        """
        Send event to external webhook endpoint.
        Task: NOTIF-07 (Webhook)
        """
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    webhook_url,
                    json=event,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code >= 400:
                    logger.warning(f"[Dispatcher] Webhook returned {resp.status_code}: {webhook_url}")
                else:
                    logger.info(f"[Dispatcher] Webhook delivered to {webhook_url}")
        except Exception as e:
            logger.error(f"[Dispatcher] Webhook failed ({webhook_url}): {e}")


class MQTTClient:
    """
    Frigate equivalent: frigate/comms/mqtt.py
    MQTT client for device integration.
    Publishes events, subscribes to PTZ commands.
    TODO NOTIF-05, NOTIF-06: implement with aiomqtt
    """
    pass
