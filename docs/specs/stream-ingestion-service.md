# Service Specification: Stream Ingestion Service (`stream-ingestion-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Backend/Cloud)  
> **Language**: Python 3.11 (FastAPI)  
> **Responsibility**: Event enrichment, frame extraction, and state management.

---

## 1. Business Logic & Responsibility
The `stream-ingestion-service` is the bridge between raw detections and high-level reasoning. It acts as the **state manager** for what is happening in a zone. It handles "debouncing" (preventing 100 alerts for one person standing still) and prepares the visual evidence for the VLM.

### Core Capabilities:
1.  **State Tracking**: Knows if a zone is currently "Active" (person detecting) or "Clear".
2.  **Debouncing**: Groups detections within a 10s window into a single "Incident".
3.  **Frame Extraction**: Connects to the camera stream to capture high-quality evidence (Frame A, B, C).
4.  **Enrichment**: Uploads frames to MinIO and adds URLs to the event payload.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: Kafka `raw-detections` topic.
-   **Output**: Kafka `vlm-requests` topic.

### 2.2 Technical Stack
-   **Framework**: FastAPI (for health probes) + `aiokafka` (Async Kafka Consumer).
-   **Storage**: MinIO SDK (for S3 uploads), Redis (for active incident state).
-   **Video**: `ffmpeg-python` or `OpenCV` for grabbing specific frames from the stream.

### 2.3 Data Lifecycle
-   **On 'Person' Detection**:
    1.  Check Redis: Is there an active incident for this Zone?
    2.  **If No**: Create new `incident_id`, capture 3 frames, create Postgres Record, upload to MinIO, push to `vlm-requests`.
    3.  **If Yes**: Update timestamp in Redis (extend timeout), ignore VLM request (don't spam AI).

---

## 3. Interfaces & APIs

### Kafka Topic: `vlm-requests`
```json
{
  "incident_id": "inc_555",
  "zone_id": "zone_A",
  "timestamp": "...",
  "image_urls": [
    "http://minio.../frame_1.jpg",
    "http://minio.../frame_2.jpg"
  ]
}
```

---

## 4. MVP Implementation Steps
1.  **Redis State**: Implement `get_active_incident(zone_id)` logic.
2.  **Snapshotter**: Write a reusable function `capture_frames(rtsp_url, count=3)`.
3.  **Uploader**: efficient upload to MinIO bucket `argus-frames`.
4.  **Integration**: Wire up the Kafka Consumer -> Logic -> Kafka Producer loop.
