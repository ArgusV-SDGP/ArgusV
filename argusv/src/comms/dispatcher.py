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
        """TODO NOTIF-05: publish to MQTT"""
        # import aiomqtt
        # async with aiomqtt.Client(host, port) as client:
        #     await client.publish(f"argus/{camera_id}/alert", json.dumps(event))
        logger.debug(f"[Dispatcher] TODO: MQTT publish for {event.get('camera_id')}")

    async def _dispatch_webpush(self, event: dict):
        """TODO: send browser push notification"""
        logger.debug(f"[Dispatcher] TODO: WebPush for {event.get('camera_id')}")


class MQTTClient:
    """
    Frigate equivalent: frigate/comms/mqtt.py
    MQTT client for device integration.
    Publishes events, subscribes to PTZ commands.
    TODO NOTIF-05, NOTIF-06: implement with aiomqtt
    """
    pass
