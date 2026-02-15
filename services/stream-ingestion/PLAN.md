# Stream Ingestion Service Development Plan

This document outlines the step-by-step plan to develop the `stream-ingestion` service.

## 1. Objectives
- **Event Consumer**: Consume `raw-detections` from `edge-gateway`.
- **Frame Extraction**: Connect to RTSP stream to grab high-quality context frames (T+0, T+1, T+2).
- **Storage**: Upload frames to MinIO.
- **Enrichment**: Create a rich payload with metadata and frame URLs.
- **Forwarding**: Publish to `vlm-requests` for the inference engine.

## 2. Architecture & Components

The service is a Python FastAPI app (for health/metrics) with a background Kafka consumer loop.

1.  **`KafkaConsumer`**: Listens to `raw-detections`.
2.  **`FrameExtractor`**: 
    -   Uses `cv2` or `ffmpeg` to grab frames from the RTSP source.
    -   *Optimization*: Maintain a ring buffer of the last few seconds of video to grab "past" frames instantly without reconnecting? 
    -   *MVP Approach*: Upon receiving a detection, connect to RTSP and grab 3 frames over 2 seconds. (Latency trade-off).
3.  **`MinIOUploader`**: Pushes bytes to S3-compatible storage and returns signed/public URLs.
4.  **`KafkaProducer`**: Output to `vlm-requests`.

## 3. Step-by-Step Implementation Plan

### Phase 1: Foundation & Infrastructure
- [ ] **Dependencies**: Update `pyproject.toml` (already has `aiokafka`, `minio`, `opencv-python-headless`).
- [ ] **MinIO Setup**: Ensure `docker-compose.yml` has MinIO and create a bucket `argus-frames` on startup (or via a script).

### Phase 2: Core Components
- [ ] **MinIO Client**: Create `src/storage.py`.
    -   Initialize `Minio` client.
    -   Implement `upload_frame(frame_bytes, object_name) -> url`.
- [ ] **Frame Extractor**: Create `src/frame_extractor.py`.
    -   Input: RTSP URL, timestamp (optional).
    -   Logic: Connect, grab 3 frames (immediately, +1s, +2s).
    -   Return: List of byte arrays or PIL images.
- [ ] **Kafka Layer**: Create `src/kafka_io.py`.
    -   `DetectionConsumer`: Deserializes JSON from `raw-detections`.
    -   `VLMRequestProducer`: Serializes JSON to `vlm-requests`.

### Phase 3: Main Processing Logic
- [ ] **Ingestion Processor**: Create `src/processor.py`.
    -   Orchestrates the flow:
        1.  Receive Event.
        2.  Extract Frames.
        3.  Upload to MinIO.
        4.  Construct `VLMRequest` payload.
        5.  Publish.

### Phase 4: Service Entrypoint
- [ ] **Main App**: Update `src/main.py`.
    -   Start FastAPI (port 8002).
    -   Start the Processor loop in `lifespan` (background task).

## 4. Specific Considerations
-   **Concurrency**: Processing video takes time. The Kafka consumer should probably process events in a thread pool or via `asyncio` to avoid lag if bursts of detections occur.
-   **Error Handling**: If RTSP connection fails, should we retry? Or send a "frameless" alert? -> For MVP, log error and skip.

## 5. Verification
-   **Frame Check**: Observe MinIO browser to see if images are appearing.
-   **Payload Check**: Consume `vlm-requests` to verify JSON structure and URL validity.
