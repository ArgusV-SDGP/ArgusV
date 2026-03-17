"""
ptz/autotrack.py — PTZ camera auto-tracking
--------------------------------------------
Tasks: PTZ-01, PTZ-02, PTZ-03

Implements automatic PTZ camera control to track detected objects:
- Track high-priority detections with PTZ cameras
- ONVIF integration for PTZ control
- Auto-return to preset after timeout
- Zone-aware tracking logic

Architecture:
  1. Detection worker emits bbox for tracked object
  2. PTZ controller calculates pan/tilt deltas
  3. ONVIF/MQTT command sent to camera
  4. After IDLE_TIMEOUT, camera returns to preset position
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import config as cfg

logger = logging.getLogger("ptz.autotrack")

# Tracking parameters
TRACK_PRIORITY_MIN = 0.6  # Only track objects with confidence >= 0.6
IDLE_TIMEOUT_SEC = 30  # Return to preset after 30s of no tracking
PTZ_MOVE_THRESHOLD = 0.1  # Don't move if target is within 10% of center


@dataclass
class PTZState:
    """Current state of a PTZ camera"""
    camera_id: str
    last_move_time: float
    tracking_object_id: Optional[str]
    current_preset: Optional[str]
    is_tracking: bool


class PTZAutoTracker:
    """
    Auto-tracking controller for PTZ cameras.

    Consumes detection events from the bus and sends PTZ commands
    via MQTT or ONVIF to keep the camera pointed at high-priority objects.

    Tasks: PTZ-01, PTZ-02, PTZ-03
    """

    def __init__(self):
        self._states: dict[str, PTZState] = {}
        self._mqtt_client: Optional[object] = None
        self._onvif_clients: dict[str, object] = {}

    async def start(self):
        """Initialize PTZ tracker and ONVIF connections"""
        logger.info("[PTZ] Auto-tracker initialized")

        # Initialize MQTT client if configured
        if cfg.MQTT_HOST:
            try:
                import aiomqtt
                self._mqtt_client = aiomqtt.Client(
                    hostname=cfg.MQTT_HOST,
                    port=cfg.MQTT_PORT,
                    username=cfg.MQTT_USER if cfg.MQTT_USER else None,
                    password=cfg.MQTT_PASS if cfg.MQTT_PASS else None,
                )
                logger.info("[PTZ] MQTT client initialized for PTZ control")
            except ImportError:
                logger.warning("[PTZ] aiomqtt not installed — MQTT PTZ control disabled")
            except Exception as e:
                logger.error(f"[PTZ] Failed to initialize MQTT: {e}")

        # TODO PTZ-02: Initialize ONVIF clients for configured PTZ cameras
        # from onvif import ONVIFCamera
        # for camera in ptz_cameras:
        #     client = ONVIFCamera(camera.ip, camera.port, camera.user, camera.pass)
        #     self._onvif_clients[camera.camera_id] = client

    async def process_detection(self, detection: dict):
        """
        Process a detection event and update PTZ state.

        Args:
            detection: Detection event with bbox, confidence, tracking_id
        """
        camera_id = detection.get("camera_id")
        tracking_id = detection.get("tracking_id")
        confidence = detection.get("confidence", 0.0)
        bbox = detection.get("bbox")

        if not camera_id or not bbox:
            return

        # Only track high-confidence detections
        if confidence < TRACK_PRIORITY_MIN:
            return

        # Get or create PTZ state
        if camera_id not in self._states:
            self._states[camera_id] = PTZState(
                camera_id=camera_id,
                last_move_time=0.0,
                tracking_object_id=None,
                current_preset=None,
                is_tracking=False,
            )

        state = self._states[camera_id]

        # Calculate target position (center of bbox)
        center_x = (bbox["x1"] + bbox["x2"]) / 2
        center_y = (bbox["y1"] + bbox["y2"]) / 2

        # Calculate delta from frame center (0.5, 0.5)
        delta_x = center_x - 0.5
        delta_y = center_y - 0.5

        # Only move if target is outside threshold
        if abs(delta_x) < PTZ_MOVE_THRESHOLD and abs(delta_y) < PTZ_MOVE_THRESHOLD:
            logger.debug(f"[PTZ] Target centered for {camera_id} — no movement needed")
            return

        # Send PTZ command
        await self._send_ptz_command(camera_id, delta_x, delta_y)

        # Update state
        state.tracking_object_id = tracking_id
        state.last_move_time = time.time()
        state.is_tracking = True

    async def _send_ptz_command(self, camera_id: str, delta_x: float, delta_y: float):
        """
        Send PTZ command via MQTT or ONVIF.

        Task: PTZ-01, PTZ-02
        """
        # Try ONVIF first if available
        if camera_id in self._onvif_clients:
            await self._send_onvif_command(camera_id, delta_x, delta_y)
        # Fall back to MQTT
        elif self._mqtt_client:
            await self._send_mqtt_ptz(camera_id, delta_x, delta_y)
        else:
            logger.debug(f"[PTZ] No PTZ control method available for {camera_id}")

    async def _send_onvif_command(self, camera_id: str, delta_x: float, delta_y: float):
        """
        Send ONVIF PTZ command.

        Task: PTZ-02 (ONVIF integration)
        """
        # TODO PTZ-02: Implement ONVIF PTZ control
        # client = self._onvif_clients[camera_id]
        # ptz_service = client.create_ptz_service()
        # request = ptz_service.create_type('RelativeMove')
        # request.Translation = {'PanTilt': {'x': delta_x, 'y': -delta_y}}
        # await ptz_service.RelativeMove(request)
        logger.info(f"[PTZ] ONVIF command: {camera_id} pan={delta_x:.2f}, tilt={delta_y:.2f}")

    async def _send_mqtt_ptz(self, camera_id: str, delta_x: float, delta_y: float):
        """
        Send MQTT PTZ command.

        Task: PTZ-01 (MQTT PTZ)
        """
        import json

        topic = f"argusv/ptz/{camera_id}/move"
        payload = {
            "delta_x": round(delta_x, 3),
            "delta_y": round(delta_y, 3),
            "timestamp": time.time(),
        }

        try:
            async with self._mqtt_client as client:
                await client.publish(topic, json.dumps(payload), qos=1)
                logger.info(f"📹 [PTZ] MQTT command sent: {camera_id} Δx={delta_x:.2f}, Δy={delta_y:.2f}")
        except Exception as e:
            logger.error(f"[PTZ] MQTT command failed: {e}")

    async def check_idle_return(self):
        """
        Return PTZ cameras to preset positions after idle timeout.

        Task: PTZ-03 (auto-return to preset)
        """
        now = time.time()

        for camera_id, state in self._states.items():
            if not state.is_tracking:
                continue

            idle_duration = now - state.last_move_time

            if idle_duration > IDLE_TIMEOUT_SEC:
                logger.info(f"[PTZ] Camera {camera_id} idle for {idle_duration:.1f}s — returning to preset")
                await self._return_to_preset(camera_id)
                state.is_tracking = False
                state.tracking_object_id = None

    async def _return_to_preset(self, camera_id: str):
        """
        Return camera to its configured preset position.

        Task: PTZ-03
        """
        state = self._states.get(camera_id)
        preset_name = state.current_preset if state else "home"

        if camera_id in self._onvif_clients:
            # TODO PTZ-03: ONVIF preset recall
            # client = self._onvif_clients[camera_id]
            # ptz_service = client.create_ptz_service()
            # ptz_service.GotoPreset({'PresetToken': preset_name})
            logger.info(f"[PTZ] ONVIF return to preset '{preset_name}' for {camera_id}")

        elif self._mqtt_client:
            import json
            topic = f"argusv/ptz/{camera_id}/preset"
            payload = {"preset": preset_name}

            try:
                async with self._mqtt_client as client:
                    await client.publish(topic, json.dumps(payload), qos=1)
                    logger.info(f"[PTZ] MQTT return to preset '{preset_name}' for {camera_id}")
            except Exception as e:
                logger.error(f"[PTZ] Failed to return to preset: {e}")


# Global tracker instance
_ptz_tracker: Optional[PTZAutoTracker] = None


async def ptz_tracker_worker():
    """
    PTZ auto-tracker background worker.

    Periodically checks for idle cameras and returns them to presets.
    """
    global _ptz_tracker
    _ptz_tracker = PTZAutoTracker()
    await _ptz_tracker.start()

    logger.info("[PTZ] Tracker worker started")

    while True:
        try:
            await _ptz_tracker.check_idle_return()
        except Exception as e:
            logger.error(f"[PTZ] Error in tracker worker: {e}", exc_info=True)

        await asyncio.sleep(10)  # Check every 10 seconds


def get_ptz_tracker() -> Optional[PTZAutoTracker]:
    """Get global PTZ tracker instance"""
    return _ptz_tracker
