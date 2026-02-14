# Service Specification: Edge Gateway Service (`edge-gateway-service`)

> **Status**: DRAFT  
> **Type**: Microservice (Edge/Local)  
> **Language**: Python 3.11  
> **Responsibility**: Camera connectivity, raw stream processing, and initial motion detection.

---

## 1. Business Logic & Responsibility
The `edge-gateway-service` is the "eyes" of ArgusV. It is the only service that communicates directly with physical cameras. Its primary goal is to **filter noise at the edge**. It must ingest high-bandwidth video streams and only pass "interesting" events (potential people/vehicles) downstream to save cloud costs and bandwidth.

### Core Capabilities:
1.  **RTSP Consumption**: Connects to multiple IP cameras via RTSP.
2.  **Motion Detection**: Uses lightweight CV (background subtraction or YOLO-nano) to detect movement.
3.  **Object Filtering**: Classifies if the moving object is a `person` or `vehicle`.
4.  **Event Dispatch**: If `person` detected → Send `detection_event` to Kafka `raw-detections` topic.

---

## 2. Engineering Requirements

### 2.1 Inputs & Outputs
-   **Input**: RTSP Streams (e.g., `rtsp://192.168.1.10:554/live`)
-   **Output**: Kafka Messages & HLS Stream (via MediaMTX sidecar)

### 2.2 Technical Stack
-   **Framework**: Python `multiprocessing` (one process per camera to prevent blocking).
-   **Vision Library**: `Ultralytics YOLOv8` (Nano model for CPU efficiency) or `OpenCV`.
-   **Config**: `cameras.yaml` (list of RTSP URLs and Zone definitions).

### 2.3 Performance Goals
-   **Latency**: Detection to Kafka < 200ms.
-   **Throughput**: Support up to 4 cameras on a standard 4-core CPU.
-   **Reliability**: Auto-reconnect to RTSP stream on failure.

---

## 3. Data Structures (Topic: `raw-detections`)

```json
{
  "event_id": "evt_uuid_123",
  "camera_id": "cam_01",
  "timestamp": "2026-02-14T12:00:00Z",
  "bbox": [100, 200, 300, 400],
  "confidence": 0.85,
  "label": "person",
  "zone_id": "zone_A"
}
```

---

## 4. MVP Implementation Steps
1.  **Skeleton**: Create a script that reads `cameras.yaml` and starts a thread/process for each.
2.  **Capture**: Use `cv2.VideoCapture` to read frames.
3.  **Inference**: Run YOLOv8n on every 5th frame (skip frames for performance).
4.  **Publish**: If class is `person` and conf > 0.5, push JSON to Kafka.
5.  **Mocking**: For dev, allow reading from a local `.mp4` file instead of RTSP.
