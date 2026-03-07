"""
ptz/autotrack.py — PTZ camera auto-tracking
---------------------------------------------
Frigate equivalent: frigate/ptz/autotrack.py

Pan-Tilt-Zoom control to follow tracked objects.
Uses ONVIF protocol to control PTZ cameras.
TODO NOTIF-07: implement
"""

import logging

logger = logging.getLogger("ptz.autotrack")


class AutoTracker:
    """
    Automatically pans/tilts camera to keep tracked object centered.
    Frigate's PTZ autotrack uses ONVIF + object centroid velocity.

    TODO NOTIF-07: implement with onvif-zeep library
    """

    def __init__(self, camera_id: str, onvif_host: str,
                 onvif_user: str, onvif_password: str):
        self.camera_id = camera_id
        self._host     = onvif_host
        self._user     = onvif_user
        self._password = onvif_password
        self._client   = None

    async def connect(self):
        """TODO: connect to ONVIF camera"""
        # from onvif import ONVIFCamera
        # self._client = ONVIFCamera(self._host, 80, self._user, self._password)
        logger.warning(f"[PTZ:{self.camera_id}] TODO NOTIF-07: ONVIF connect not implemented")

    async def track_object(self, norm_x: float, norm_y: float,
                           bbox_width: float, bbox_height: float):
        """
        Move camera so detected object is centred.
        norm_x, norm_y: normalised position 0..1
        bbox_width, bbox_height: normalised bbox size
        TODO NOTIF-07
        """
        error_x = norm_x - 0.5  # distance from frame centre
        error_y = norm_y - 0.5
        # TODO: PID controller → PTZ speed
        logger.debug(f"[PTZ:{self.camera_id}] TODO: move ({error_x:.2f}, {error_y:.2f})")

    async def stop(self):
        """Stop PTZ movement."""
        logger.debug(f"[PTZ:{self.camera_id}] Stop")
