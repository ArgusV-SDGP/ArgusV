"""
workers/pipeline_worker.py
---------------------------
Replaces: stream-ingestion + vlm-inference + decision-engine consumers
as simple async tasks reading from bus queues.

All three stages run in the same event loop — no network hops.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
import json
import base64
from typing import Optional

import config as cfg
from bus import bus
from events.maintainer import EventMaintainer

logger = logging.getLogger("pipeline-worker")

# Global maintainer to track object lifecycles across all cameras
maintainer = EventMaintainer()


def _build_snapshot_url(camera_id: Optional[str], event_id: Optional[str]) -> Optional[str]:
    if not camera_id or not event_id:
        return None
    return f"/recordings/snapshots/{camera_id}/{camera_id}_{event_id}.jpg"

# ── Redis rate limiter (lazy init) ────────────────────────────────────────────

_redis = None


async def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis.asyncio as aioredis
            _redis = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)
            await _redis.ping()
            logger.info("[Pipeline] Redis connected — rate limiting active")
        except Exception as e:
            logger.warning(f"[Pipeline] Redis unavailable, rate limiting disabled: {e}")
            _redis = None
    return _redis


async def _is_rate_limited(camera_id: str, zone_name: str, object_class: str) -> bool:
    r = await _get_redis()
    if r is None:
        return False
    key = f"argus:rate:{camera_id}:{zone_name}:{object_class}"
    try:
        return bool(await r.exists(key))
    except Exception:
        return False


async def _set_rate_limit(camera_id: str, zone_name: str, object_class: str):
    r = await _get_redis()
    if r is None:
        return
    key = f"argus:rate:{camera_id}:{zone_name}:{object_class}"
    try:
        await r.set(key, "1", ex=cfg.RATE_LIMIT_TTL)
        logger.debug(f"[Pipeline] Rate limit set: {object_class} in '{zone_name}' for {cfg.RATE_LIMIT_TTL}s")
    except Exception as e:
        logger.debug(f"[Pipeline] Redis set failed: {e}")


# ── Stage 1: VLM Request Builder ─────────────────────────────────────────────
# Consumes raw_detections → enriches with frame url → puts on vlm_requests
# (Replaces stream-ingestion service)

async def stream_ingestion_worker():
    """
    Consumes raw_detections.
    For LOITERING or high-confidence events: sends to VLM.
    For low-confidence / UPDATE: sends directly to WebSocket (no VLM cost).
    """
    logger.info("[Pipeline] stream-ingestion worker started")
    while True:
        event: dict = await bus.raw_detections.get()
        try:
            event_type = event.get("event_type", "DETECTED")
            confidence = event.get("confidence", 0.0)

            # 1. Update Lifecycle Maintainer (Locks segments, triggers clips on END)
            await maintainer.process(event)

            # 2. Tap START events into the snapshot worker
            if event_type == "START" and event.get("trigger_frame_b64"):
                await bus.snapshots.put(event)

            # 3. Decide whether VLM analysis is needed
            needs_vlm = (
                event_type in ("START", "LOITERING") and
                confidence >= cfg.CONF_THRESHOLD and
                cfg.GENAI_PROVIDER != "disabled"
            )

            # Always push to WebSocket immediately (fast alert path)
            alert = {
                **event,
                "status":       "analyzing" if needs_vlm else "complete",
                "threat_level": "PENDING"   if needs_vlm else None,
            }
            alert.pop("trigger_frame_b64", None)  # strip large payload for WS
            await bus.alerts_ws.put({"type": "fast_alert", **alert})

            if needs_vlm:
                await bus.vlm_requests.put(event)
            else:
                # No VLM → We STILL need to log it in the DB via Decision Engine
                # if it's a high-confidence start or loiter
                if event_type in ("START", "LOITERING", "DETECTED") and confidence >= cfg.CONF_THRESHOLD:
                     await bus.vlm_results.put({
                         **event,
                         "vlm": {
                             "threat_level": "LOW",
                             "is_threat": False,
                             "summary": None,   # no VLM ran — keep null so RAG ignores it
                         }
                     })
                
                # Forward to simple action log
                if confidence >= cfg.CONF_THRESHOLD:
                    await bus.actions.put({
                        **event,
                        "action_type":  "LOG",
                        "threat_level": "LOW",
                        "is_threat":    False,
                    })
        except Exception as e:
            logger.error(f"[stream-ingestion] Error: {e}", exc_info=True)
        finally:
            bus.raw_detections.task_done()


# ── Stage 2: VLM Inference ───────────────────────────────────────────────────
# Consumes vlm_requests → calls OpenAI → puts on vlm_results
# (Replaces vlm-inference service)

_vlm_semaphore: Optional[asyncio.Semaphore] = None


async def vlm_inference_worker():
    global _vlm_semaphore
    _vlm_semaphore = asyncio.Semaphore(cfg.VLM_MAX_WORKERS)
    logger.info("[Pipeline] VLM inference worker started")
    while True:
        event: dict = await bus.vlm_requests.get()
        asyncio.create_task(_run_vlm(event))
        bus.vlm_requests.task_done()


async def _run_vlm(event: dict):
    from genai.manager import provider as genai_provider
    async with _vlm_semaphore:
        try:
            result = await genai_provider.describe(event)
            await bus.vlm_results.put({**event, "vlm": result})

            # Live update to dashboard with VLM result
            await bus.alerts_ws.put({
                "type":         "vlm_update",
                "event_id":     event.get("event_id"),
                "camera_id":    event.get("camera_id"),
                "zone_name":    event.get("zone_name"),
                "object_class": event.get("object_class"),
                "threat_level": result.get("threat_level"),
                "is_threat":    result.get("is_threat"),
                "summary":      result.get("summary"),
                "confidence":   event.get("confidence"),
                "timestamp":    event.get("timestamp"),
                "status":       "complete",
            })
        except Exception as e:
            logger.error(f"[VLM] Inference error: {e}", exc_info=True)
            # Push failure so alert card doesn't spin forever
            await bus.alerts_ws.put({
                "type":      "vlm_update",
                "event_id":  event.get("event_id"),
                "status":    "error",
                "summary":   "VLM analysis failed",
            })


async def _call_openai(event: dict) -> dict:
    """Tiered GPT-4o-mini triage → GPT-4o full analysis."""
    import httpx

    frames_b64 = []
    if event.get("trigger_frame_b64"):
        frames_b64.append(event["trigger_frame_b64"])

    if not cfg.OPENAI_API_KEY:
        return {"threat_level": "UNKNOWN", "is_threat": False, "summary": "No API key"}

    headers = {
        "Authorization": f"Bearer {cfg.OPENAI_API_KEY}",
        "Content-Type":  "application/json",
    }

    from genai.manager import _build_prompts_async
    triage_prompt, full_prompt = await _build_prompts_async(event)

    async with httpx.AsyncClient(timeout=30) as client:
        # Triage call (cheap)
        msgs = [{"role": "user", "content": triage_prompt}]
        if cfg.USE_TIERED_VLM and frames_b64:
            msgs = [{"role": "user", "content": [
                {"type": "text",      "text": triage_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{frames_b64[0]}", "detail": "low"}},
            ]}]

        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={"model": cfg.VLM_TRIAGE_MODEL, "messages": msgs, "max_tokens": 10},
        )
        resp.raise_for_status()
        triage = resp.json()["choices"][0]["message"]["content"].strip().upper()

        if triage == "NO" and cfg.USE_TIERED_VLM:
            return {"threat_level": "LOW", "is_threat": False, "summary": "Triage: not suspicious"}

        # Full analysis call
        content_parts = [{"type": "text", "text": full_prompt}]
        if frames_b64:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{frames_b64[0]}", "detail": "high"},
            })

        resp2 = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json={"model": cfg.VLM_MODEL, "messages": [{"role": "user", "content": content_parts}], "max_tokens": 200},
        )
        resp2.raise_for_status()
        content = resp2.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON response
        import re
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {"threat_level": "MEDIUM", "is_threat": True, "summary": content[:200]}


# ── Stage 3: Decision Engine ─────────────────────────────────────────────────
# Consumes vlm_results → writes to DB → puts on actions
# (Replaces decision-engine Kafka consumer)

async def decision_engine_worker():
    logger.info("[Pipeline] Decision engine worker started")
    while True:
        event: dict = await bus.vlm_results.get()
        try:
            await _process_decision(event)
        except Exception as e:
            logger.error(f"[Decision] Error: {e}", exc_info=True)
        finally:
            bus.vlm_results.task_done()


async def _process_decision(event: dict):
    from db.connection import get_db_session
    from db.models import Detection, Incident, Segment
    from datetime import datetime, timezone
    from sqlalchemy import select
    import uuid
    from genai.manager import embed_text

    vlm = event.get("vlm", {})
    is_threat    = vlm.get("is_threat", False)
    threat_level = vlm.get("threat_level", "LOW")
    summary_text = vlm.get("summary", "")

    # Embed the VLM description for semantic search (non-blocking — None if unavailable)
    embedding = await embed_text(summary_text) if summary_text else None
    if embedding:
        logger.debug(f"[Pipeline] Embedded description ({len(embedding)} dims): {summary_text[:60]}...")

    # DB write is best-effort — a failure must NOT block notification dispatch
    try:
        async with get_db_session() as db:
            det_ts = datetime.fromtimestamp(event.get("timestamp", time.time()), timezone.utc).replace(tzinfo=None)
            thumbnail_url = _build_snapshot_url(event.get("camera_id"), event.get("event_id"))

            # Find the segment that covers this detection's timestamp
            seg_result = await db.execute(
                select(Segment.segment_id)
                .where(Segment.camera_id == event.get("camera_id"))
                .where(Segment.start_time <= det_ts)
                .where(Segment.end_time >= det_ts)
                .limit(1)
            )
            segment_id = seg_result.scalar_one_or_none()

            # Write detection row
            det = Detection(
                event_id     = event.get("event_id"),
                camera_id    = event.get("camera_id"),
                segment_id   = segment_id,
                detected_at  = det_ts,
                object_class = event.get("object_class", ""),
                confidence   = event.get("confidence", 0.0),
                zone_id      = event.get("zone_id"),
                zone_name    = event.get("zone_name"),
                event_type   = event.get("event_type"),
                track_id     = event.get("track_id"),
                dwell_sec    = event.get("dwell_sec", 0),
                bbox_x1      = event.get("bbox", {}).get("x1"),
                bbox_y1      = event.get("bbox", {}).get("y1"),
                bbox_x2      = event.get("bbox", {}).get("x2"),
                bbox_y2      = event.get("bbox", {}).get("y2"),
                thumbnail_url= thumbnail_url,
                is_threat    = is_threat,
                threat_level = threat_level,
                vlm_summary  = summary_text,
                vlm_embedding= embedding,
            )
            db.add(det)

            # Create incident for HIGH/MEDIUM threats and link detection → incident
            if is_threat and threat_level in ("HIGH", "MEDIUM"):
                inc = Incident(
                    camera_id    = event.get("camera_id"),
                    zone_id      = event.get("zone_id"),
                    zone_name    = event.get("zone_name"),
                    object_class = event.get("object_class"),
                    threat_level = threat_level,
                    summary      = vlm.get("summary"),
                    status       = "OPEN",
                    detected_at  = datetime.utcfromtimestamp(event.get("timestamp", time.time())),
                    metadata_json= event,
                )
                db.add(inc)
                await db.flush()          # assign inc.incident_id before linking
                det.incident_id = inc.incident_id

            await db.commit()
    except Exception as e:
        logger.error(f"[Decision] DB write failed (notification will still fire): {e}", exc_info=True)

    # Always forward threats to notification + actuation regardless of DB outcome
    if is_threat:
        await bus.actions.put({
            **event,
            "action_type":  "ALERT",
            "threat_level": threat_level,
            "summary":      vlm.get("summary"),
        })


# ── Stage 4: Notification + Actuation ────────────────────────────────────────

async def notification_worker():
    logger.info("[Pipeline] Notification worker started")
    while True:
        action: dict = await bus.actions.get()
        try:
            if action.get("action_type") == "ALERT":
                await _dispatch_notifications(action)
        except Exception as e:
            logger.error(f"[Notification] Error: {e}", exc_info=True)
        finally:
            bus.actions.task_done()


async def _dispatch_notifications(action: dict):
    """Dispatch notifications per DB-configured NotificationRule rows."""
    from db.connection import get_db_session
    from db.models import NotificationRule
    from sqlalchemy import select

    threat_level = action.get("threat_level", "LOW")
    event_zone_id = str(action.get("zone_id", "")) if action.get("zone_id") else None
    dispatched = False

    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(NotificationRule).where(NotificationRule.severity == threat_level)
            )
            rules = result.scalars().all()
    except Exception as e:
        logger.warning(f"[Notification] DB query failed, falling back to env config: {e}")
        rules = []

    for rule in rules:
        # "global" rules match all zones; zone-specific rules match only the event's zone
        if rule.zone_id != "global" and rule.zone_id != event_zone_id:
            continue

        rule_cfg = rule.config or {}
        for channel in (rule.channels or []):
            try:
                if channel == "telegram" and cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID:
                    await _send_telegram(action)
                    dispatched = True
                elif channel == "slack" and cfg.SLACK_BOT_TOKEN:
                    await _send_slack(action)
                    dispatched = True
                elif channel == "webhook":
                    webhook_url = rule_cfg.get("webhook_url")
                    if webhook_url:
                        await _send_webhook(action, webhook_url)
                        dispatched = True
            except Exception as e:
                logger.error(f"[Notification] Channel '{channel}' error: {e}")

    # Fallback: no DB rules matched → use env-based credentials if available
    if not dispatched:
        if cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID:
            await _send_telegram(action)
        elif cfg.SLACK_BOT_TOKEN:
            await _send_slack(action)


async def _send_slack(action: dict):
    import httpx
    threat  = action.get("threat_level", "?")
    zone    = action.get("zone_name", "?")
    obj     = action.get("object_class", "?")
    summary = action.get("summary", "")
    emoji   = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get(threat, "⚪")
    text    = f"{emoji} *{threat} THREAT* — {obj.capitalize()} in *{zone}*\n>{summary}"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {cfg.SLACK_BOT_TOKEN}"},
            json={"channel": cfg.SLACK_CHANNEL_ID, "text": text},
        )
    logger.info(f"[Slack] Sent alert: {threat} in {zone}")


async def _send_webhook(action: dict, url: str):
    import httpx
    payload = {
        "threat_level":  action.get("threat_level"),
        "zone_name":     action.get("zone_name"),
        "object_class":  action.get("object_class"),
        "camera_id":     action.get("camera_id"),
        "summary":       action.get("summary"),
        "timestamp":     action.get("timestamp"),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
    logger.info(f"[Webhook] Sent alert to {url}: {resp.status_code}")


async def _send_telegram(action: dict):
    import httpx
    import base64
    threat  = action.get("threat_level", "?")
    zone    = action.get("zone_name", "?")
    obj     = action.get("object_class", "?")
    summary = action.get("summary", "")
    emoji   = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get(threat, "⚪")
    caption = f"{emoji} *{threat} THREAT* — {obj.capitalize()} in *{zone}*\n{summary}"

    frame_b64 = action.get("trigger_frame_b64")
    async with httpx.AsyncClient(timeout=30) as client:
        if frame_b64:
            photo_bytes = base64.b64decode(frame_b64)
            await client.post(
                f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={"chat_id": cfg.TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": ("snapshot.jpg", photo_bytes, "image/jpeg")},
            )
        else:
            await client.post(
                f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": caption, "parse_mode": "Markdown"},
            )
    logger.info(f"[Telegram] Sent alert: {threat} in {zone}")
