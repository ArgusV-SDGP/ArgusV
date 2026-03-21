"""
workers/embedding_worker.py — Video Frame Embedding Indexer
------------------------------------------------------------
Tasks: VLM-07, VLM-08

Consumes video frames and detection events, generates CLIP embeddings,
and indexes them in Milvus for semantic search.

Architecture:
  1. Listens to bus.segment_events (new video segments)
  2. Samples frames from each segment
  3. Generates CLIP embeddings
  4. Stores in Milvus with metadata
  5. Also indexes detection events with multimodal embeddings
"""

import asyncio
import base64
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

import config as cfg
from bus import bus
from embeddings.embeddings import get_embedding_manager
from embeddings.milvus_client import get_milvus_client

logger = logging.getLogger("embedding-worker")


async def embedding_worker():
    """
    Main embedding worker loop.

    Consumes segment_complete events and indexes frames into Milvus.
    """
    logger.info("[Embedding] Worker started")

    # Initialize embedding manager and Milvus client
    embed_mgr = get_embedding_manager()
    milvus = await get_milvus_client()

    if not milvus._connected:
        logger.warning("[Embedding] Milvus not connected — worker will skip indexing")

    while True:
        try:
            # Wait for segment complete event
            event = await asyncio.wait_for(
                bus.segment_events.get(),
                timeout=10.0  # Poll every 10s to check connection
            )

            if event.get("event_type") == "SEGMENT_COMPLETE":
                await _index_segment(event, embed_mgr, milvus)

            bus.segment_events.task_done()

        except asyncio.TimeoutError:
            # Check if we need to reconnect to Milvus
            if not milvus._connected:
                logger.info("[Embedding] Attempting to reconnect to Milvus...")
                await milvus.connect()

        except Exception as e:
            logger.error(f"[Embedding] Error: {e}", exc_info=True)
            await asyncio.sleep(1)


async def _index_segment(event: dict, embed_mgr, milvus):
    """
    Index video segment frames into Milvus.

    Strategy:
    - Sample frames at EMBED_FRAME_SAMPLE_RATE (e.g., 1 frame per 2 seconds)
    - Generate CLIP embeddings for each sampled frame
    - Store in Milvus with segment metadata
    """
    segment_path = event.get("path")
    camera_id = event.get("camera_id")
    start_time = event.get("start_time")  # ISO format
    segment_id = Path(segment_path).stem

    if not segment_path or not Path(segment_path).exists():
        logger.warning(f"[Embedding] Segment file not found: {segment_path}")
        return

    logger.info(f"[Embedding] Indexing segment: {segment_id}")

    # Parse start timestamp
    start_dt = datetime.fromisoformat(start_time)
    start_ts = int(start_dt.timestamp())

    # Open video file
    cap = cv2.VideoCapture(segment_path)
    if not cap.isOpened():
        logger.error(f"[Embedding] Failed to open segment: {segment_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / fps

    # Calculate sample rate (e.g., 1 frame every 2 seconds)
    sample_interval = int(fps * cfg.EMBED_FRAME_SAMPLE_SEC)
    if sample_interval < 1:
        sample_interval = 1

    indexed_count = 0
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Sample frames at interval
        if frame_idx % sample_interval == 0:
            try:
                # Generate frame ID
                frame_offset_sec = frame_idx / fps
                frame_ts = start_ts + int(frame_offset_sec)
                frame_id = f"{segment_id}_f{frame_idx:06d}"

                # Generate CLIP embedding
                embedding = embed_mgr.encode_image(frame)

                # Insert into Milvus
                await milvus.insert_frame_embedding(
                    frame_id=frame_id,
                    camera_id=camera_id,
                    timestamp=frame_ts,
                    embedding=embedding,
                    segment_id=segment_id,
                    has_detection=False,  # Will be updated when detections are linked
                    detection_classes="",
                )

                indexed_count += 1

            except Exception as e:
                logger.error(f"[Embedding] Failed to index frame {frame_idx}: {e}")

        frame_idx += 1

    cap.release()

    logger.info(
        f"[Embedding] Indexed {indexed_count} frames from segment {segment_id} "
        f"({duration_sec:.1f}s, {total_frames} total frames)"
    )


async def detection_embedding_worker():
    """
    Worker that indexes detection events into Milvus.

    Consumes VLM results and creates multimodal embeddings
    (image + text summary) for semantic search.
    """
    logger.info("[DetectionEmbedding] Worker started")

    embed_mgr = get_embedding_manager()
    milvus = await get_milvus_client()

    while True:
        try:
            # Wait for VLM result
            vlm_result = await bus.vlm_results.get()

            if vlm_result.get("embedding_enabled", True):
                await _index_detection(vlm_result, embed_mgr, milvus)

            bus.vlm_results.task_done()

        except Exception as e:
            logger.error(f"[DetectionEmbedding] Error: {e}", exc_info=True)
            await asyncio.sleep(1)


async def _index_detection(vlm_result: dict, embed_mgr, milvus):
    """
    Index detection event with multimodal embedding.

    Combines:
    - CLIP image embedding from trigger frame
    - CLIP text embedding from VLM summary
    """
    detection_id = vlm_result.get("detection_id")
    incident_id = vlm_result.get("incident_id")
    camera_id = vlm_result.get("camera_id")
    zone_name = vlm_result.get("zone_name", "unknown")
    timestamp = int(vlm_result.get("timestamp", time.time()))
    object_class = vlm_result.get("object_class", "unknown")
    threat_level = vlm_result.get("threat_level", "UNKNOWN")
    summary = vlm_result.get("summary", "")
    trigger_frame_b64 = vlm_result.get("trigger_frame_b64")

    if not detection_id:
        return

    try:
        # Generate multimodal embedding
        if trigger_frame_b64 and summary:
            # Decode frame
            frame = embed_mgr._base64_to_array(trigger_frame_b64)

            # Combined image + text embedding
            embedding = embed_mgr.encode_multimodal(frame, summary, alpha=0.6)

        elif trigger_frame_b64:
            # Image only
            frame = embed_mgr._base64_to_array(trigger_frame_b64)
            embedding = embed_mgr.encode_image(frame)

        elif summary:
            # Text only
            embedding = embed_mgr.encode_text(summary)

        else:
            logger.warning(f"[DetectionEmbedding] No image or summary for {detection_id}")
            return

        # Insert into Milvus
        await milvus.insert_detection_embedding(
            detection_id=detection_id,
            incident_id=incident_id or "",
            camera_id=camera_id,
            zone_name=zone_name,
            timestamp=timestamp,
            embedding=embedding,
            object_class=object_class,
            threat_level=threat_level,
            summary=summary,
        )

        logger.info(f"[DetectionEmbedding] Indexed detection {detection_id} ({threat_level})")

    except Exception as e:
        logger.error(f"[DetectionEmbedding] Failed to index {detection_id}: {e}", exc_info=True)
