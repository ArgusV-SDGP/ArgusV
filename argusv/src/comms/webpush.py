"""
comms/webpush.py — Browser Push Notifications
----------------------------------------------
Task: NOTIF-07 (WebPush)

Implements WebPush notifications for browser alerts.
Uses the Web Push protocol to send notifications to subscribed browsers.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("webpush")


class WebPushClient:
    """
    WebPush notification client for browser push notifications.

    Usage:
        1. User subscribes via browser (service worker)
        2. Subscription saved to DB (PushSubscription model)
        3. On HIGH threat → send push notification to all subscriptions
    """

    def __init__(self, vapid_private_key: Optional[str] = None, vapid_public_key: Optional[str] = None):
        self.vapid_private_key = vapid_private_key
        self.vapid_public_key = vapid_public_key
        self._enabled = bool(vapid_private_key and vapid_public_key)

        if not self._enabled:
            logger.info("[WebPush] VAPID keys not configured — WebPush disabled")
        else:
            logger.info("[WebPush] Client initialized with VAPID keys")

    async def send_notification(self, event: dict):
        """
        Send push notification for an event to all subscribed browsers.

        Args:
            event: Event payload containing incident/alert data
        """
        if not self._enabled:
            logger.debug("[WebPush] Disabled — skipping notification")
            return

        try:
            from pywebpush import webpush, WebPushException
        except ImportError:
            logger.warning("[WebPush] pywebpush not installed — run: pip install pywebpush")
            return

        # Query all active push subscriptions from DB
        from db.connection import get_db_sync
        from db.models import PushSubscription

        db = get_db_sync()
        try:
            subscriptions = db.query(PushSubscription).filter(
                PushSubscription.active == True
            ).all()

            if not subscriptions:
                logger.debug("[WebPush] No active subscriptions")
                return

            # Prepare notification payload
            notification_data = {
                "title": f"ArgusV Alert: {event.get('threat_level', 'UNKNOWN')}",
                "body": event.get("summary", "New security incident detected"),
                "icon": "/static/icon-192.png",
                "badge": "/static/badge-72.png",
                "data": {
                    "incident_id": event.get("incident_id"),
                    "camera_id": event.get("camera_id"),
                    "zone_name": event.get("zone_name"),
                    "threat_level": event.get("threat_level"),
                    "timestamp": event.get("timestamp"),
                    "url": f"/incidents.html?incident_id={event.get('incident_id')}",
                },
            }

            # Send to each subscription
            sent_count = 0
            failed_subscriptions = []

            for sub in subscriptions:
                try:
                    subscription_info = {
                        "endpoint": sub.endpoint,
                        "keys": {
                            "p256dh": sub.p256dh_key,
                            "auth": sub.auth_secret,
                        }
                    }

                    webpush(
                        subscription_info=subscription_info,
                        data=json.dumps(notification_data),
                        vapid_private_key=self.vapid_private_key,
                        vapid_claims={
                            "sub": "mailto:admin@argusv.local"
                        }
                    )

                    sent_count += 1
                    logger.debug(f"[WebPush] Notification sent to subscription {sub.subscription_id}")

                except WebPushException as e:
                    logger.warning(f"[WebPush] Failed to send to subscription {sub.subscription_id}: {e}")
                    if e.response and e.response.status_code in (404, 410):
                        # Subscription no longer valid
                        failed_subscriptions.append(sub)

            # Deactivate failed subscriptions
            for sub in failed_subscriptions:
                sub.active = False
                logger.info(f"[WebPush] Deactivated invalid subscription {sub.subscription_id}")

            if failed_subscriptions:
                db.commit()

            logger.info(f"📬 [WebPush] Sent {sent_count} notifications ({len(failed_subscriptions)} failed)")

        except Exception as e:
            logger.error(f"[WebPush] Error sending notifications: {e}", exc_info=True)
        finally:
            db.close()


    async def subscribe(self, subscription_data: dict, user_id: Optional[str] = None) -> str:
        """
        Register a new push subscription.

        Args:
            subscription_data: Browser subscription object (endpoint, keys)
            user_id: Optional user ID to associate with subscription

        Returns:
            Subscription ID
        """
        import uuid
        from db.connection import get_db_sync
        from db.models import PushSubscription

        db = get_db_sync()
        try:
            subscription = PushSubscription(
                subscription_id=uuid.uuid4(),
                user_id=user_id,
                endpoint=subscription_data["endpoint"],
                p256dh_key=subscription_data["keys"]["p256dh"],
                auth_secret=subscription_data["keys"]["auth"],
                active=True,
            )

            db.add(subscription)
            db.commit()
            db.refresh(subscription)

            logger.info(f"[WebPush] New subscription registered: {subscription.subscription_id}")
            return str(subscription.subscription_id)

        except Exception as e:
            logger.error(f"[WebPush] Failed to register subscription: {e}")
            db.rollback()
            raise
        finally:
            db.close()


    async def unsubscribe(self, subscription_id: str):
        """
        Deactivate a push subscription.

        Args:
            subscription_id: Subscription UUID to deactivate
        """
        from db.connection import get_db_sync
        from db.models import PushSubscription
        import uuid

        db = get_db_sync()
        try:
            sub = db.query(PushSubscription).filter(
                PushSubscription.subscription_id == uuid.UUID(subscription_id)
            ).first()

            if sub:
                sub.active = False
                db.commit()
                logger.info(f"[WebPush] Subscription deactivated: {subscription_id}")
            else:
                logger.warning(f"[WebPush] Subscription not found: {subscription_id}")

        except Exception as e:
            logger.error(f"[WebPush] Failed to unsubscribe: {e}")
            db.rollback()
        finally:
            db.close()
