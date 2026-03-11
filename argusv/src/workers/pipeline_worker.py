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
import json
import base64
from typing import Optional

import config as cfg
from bus import bus
from workers.watchdog_worker import record_heartbeat

logger = logging.getLogger("pipeline-worker")


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
        record_heartbeat("stream-ingestion")
        event: dict = await bus.raw_detections.get()
        try:
            event_type = event.get("event_type", "DETECTED")
            confidence = event.get("confidence", 0.0)

            # Tap START events into the snapshot worker
            if event_type == "START" and event.get("trigger_frame_b64"):
                await bus.snapshots.put(event)

            # Decide whether VLM analysis is needed
            needs_vlm = (
                event_type in ("START", "LOITERING") and
                confidence >= cfg.CONF_THRESHOLD and
                bool(cfg.OPENAI_API_KEY)
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
                # No VLM → push final result straight to actions
                if event.get("confidence", 0) >= 0.6:
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
        record_heartbeat("vlm-inference")
        event: dict = await bus.vlm_requests.get()
        asyncio.create_task(_run_vlm(event))
        bus.vlm_requests.task_done()


async def _run_vlm(event: dict):
    async with _vlm_semaphore:
        try:
            result = await _call_openai(event)
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

    zone_name    = event.get("zone_name", "")
    object_class = event.get("object_class", "")
    dwell_sec    = event.get("dwell_sec", 0)
    event_type   = event.get("event_type", "")

    triage_prompt = (
        f"Security camera alert: {object_class} detected in '{zone_name}'. "
        f"Event type: {event_type}. Dwell time: {dwell_sec}s. "
        "Is this worth escalating? Reply ONE word: YES or NO."
    )

    full_prompt = (
        f"You are a security analyst reviewing camera footage. "
        f"A {object_class} was detected in '{zone_name}' (dwell: {dwell_sec}s, type: {event_type}). "
        "Analyse this scene. Respond JSON: "
        '{"threat_level":"HIGH|MEDIUM|LOW","is_threat":true|false,"summary":"<1 sentence>","recommended_action":"ALERT|MONITOR|IGNORE"}'
    )

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
        record_heartbeat("decision-engine")
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
    from datetime import datetime
    import uuid

    vlm = event.get("vlm", {})
    is_threat    = vlm.get("is_threat", False)
    threat_level = vlm.get("threat_level", "LOW")

    async with get_db_session() as db:
        # Write detection row
        det = Detection(
            event_id     = event.get("event_id"),
            camera_id    = event.get("camera_id"),
            detected_at  = datetime.utcfromtimestamp(event.get("timestamp", time.time())),
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
            is_threat    = is_threat,
            threat_level = threat_level,
            vlm_summary  = vlm.get("summary"),
        )
        db.add(det)

        # Create incident for HIGH/MEDIUM threats
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

        await db.commit()

    # Forward to notification + actuation
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
        record_heartbeat("notification")
        action: dict = await bus.actions.get()
        try:
            if action.get("action_type") == "ALERT" and cfg.SLACK_BOT_TOKEN:
                await _send_slack(action)
        except Exception as e:
            logger.error(f"[Notification] Error: {e}", exc_info=True)
        finally:
            bus.actions.task_done()


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
