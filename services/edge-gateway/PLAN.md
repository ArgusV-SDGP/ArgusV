# Edge Gateway Development Plan

This document outlines the step-by-step plan to develop the `edge-gateway` service for ArgusV.

## 1. Objectives
- **RTSP Ingestion**:Reliably consume video streams from IP cameras.
- **Real-time Detection**: Perform object detection (Person, Vehicle) using YOLOv8-nano.
- **Event Forwarding**: Publish detection events to Kafka (`raw-detections`).
- **Configuration**: Dynamically load zone/rule configs via Redis/Kafka.
- **Dashboard Streaming**: Provide low-latency HLS streams for the frontend (via MediaMTX).

## 2. Architecture & Components
The Edge Gateway stack consists of two main components running on the edge node:

1.  **`edge-gateway-service` (Python)**:
    -   Reads RTSP stream using OpenCV.
    -   Runs YOLOv8 inference.
    -   Sends "Detections" to Kafka.
    -   *Does NOT re-stream video.*

2.  **`mediamtx` (Sidecar Service)**:
    -   Connects to the *same* RTSP source (or acts as the RTSP server).
    -   Transmutes RTSP -> HLS (Low Latency).
    -   Serves the stream to the Dashboard.

## 3. Step-by-Step Implementation Plan

### Phase 1: Foundation & Dependencies
- [ ] **Dependencies**: Update `pyproject.toml` to include `ultralytics`, `opencv-python-headless`, `confluent-kafka`.
- [ ] **Docker**: Ensure `Dockerfile` installs system dependencies for OpenCV (`libgl1`, `libglib2.0-0`).

### Phase 2: Core IO (Camera & Kafka)
- [ ] **RTSP Handler**: Create `src/stream_reader.py`.
    -   Use `cv2.VideoCapture` in a separate thread.
    -   Implement a "latest frame" buffer to ensure the detector always gets the newest frame (drop old ones).
    -   Handle automatic reconnection on stream loss.
- [ ] **Kafka Producer**: Create `src/event_publisher.py`.
    -   Wrap `confluent_kafka.Producer`.
    -   Define the JSON schema for detection events.

### Phase 3: Detection Engine
- [ ] **YOLO Integration**: Create `src/detector.py`.
    -   Load `yolov8n.pt` model.
    -   Implement `detect(frame)` method returning bounding boxes and classes.
    -   Filter results: Only keep "person" class (ID 0) or user-configured classes.
    -   Optimize: Run inference every N frames (e.g., 5 FPS) instead of full 30 FPS if hardware is limited.

### Phase 4: Configuration Management
- [ ] **Config Loader**: Enhance `src/config_loader.py`.
    -   Load camera URLs and Zone definitions from Redis or localized JSON backup.
    -   Listen for `config-updates` on Redis Pub/Sub to reload active zones/masks without restart.

### Phase 5: Dashboard Streaming (Sidecar)
- [ ] **MediaMTX Config**: Create `mediamtx.yml` in the root or a config dir.
    -   Map RTSP streams to flexible HLS paths.
    -   Tune for Low Latency (HLL).
- [ ] **Orchestration**: Update `docker-compose.yml` to include the `mediamtx` service linked to the edge gateway network.

### Phase 6: Main Loop & Orchestration
- [ ] **Main Loop**: Update `src/main.py`.
    -   Initialize Config, Producer, Detector.
    -   Start Stream Reader definitions.
    -   Loop:
        -   Get latest frame.
        -   Run Detector.
        -   If detection found -> Publish Event to Kafka.
        -   Sleep/Wait to maintain target FPS.

## 4. Verification Steps

### Unit Testing
-   Test `ConfigLoader` with mock Redis.
-   Test `Detector` with static image files.

### Integration Testing
-   **Stream**: Verify `mediamtx` HLS stream plays in VLC/Browser (`http://localhost:8888/cam/index.m3u8`).
-   **Detection**: Walk in front of camera -> Check Kafka for `raw-detections` message.
-   **Config**: Update Redis key -> Verify log message "Config reloaded".

## 5. Directory Structure
```
services/edge-gateway/
├── Dockerfile
├── pyproject.toml
├── mediamtx.yml          <-- NEW: Streaming config
├── src/
│   ├── main.py           # Entrypoint
│   ├── config_loader.py  # Existing
│   ├── stream_reader.py  # NEW: OpenCV wrapper
│   ├── detector.py       # NEW: YOLO wrapper
│   └── producer.py       # NEW: Kafka wrapper
```
