# ArgusV Complete System Flow Diagram

This document describes architecture and execution flows for full project scope.

## 1) Full Architecture

```mermaid
flowchart LR
  CAM[RTSP Cameras] --> FB[FrameBuffer]
  FB --> MG[Motion Gate]
  MG --> DET[YOLOv8 + ByteTrack]
  DET --> ZM[ZoneMatcher]
  ZM --> DW[Dwell Tracker]
  DW --> QRAW[(bus.raw_detections)]

  QRAW --> ING[stream_ingestion_worker]
  ING --> QWS[(bus.alerts_ws)]
  ING --> QVLM[(bus.vlm_requests)]
  ING --> QA[(bus.actions)]

  QVLM --> VLM[vlm_inference_worker]
  VLM --> QVRES[(bus.vlm_results)]
  VLM --> QWS

  QVRES --> DEC[decision_engine_worker]
  DEC --> DB[(PostgreSQL)]
  DEC --> QA

  QA --> NOTIF[notification_worker]
  QA --> ACT[actuation_worker]
  ACT --> MQTT[(MQTT Broker)]
  ACT --> PTZ[PTZ Control]

  QWS --> FAN[ws_handler fan_out]
  FAN --> DASH[Dashboard Clients]

  REC[recording_worker] --> FS[/recordings segments/]
  REC --> DB
  REC --> EVM[events/maintainer + snapshot worker]
  EVM --> DB

  API[FastAPI API + static UI] --> DB
  API --> REDIS[(Redis)]
  API --> DASH

  AUTH[JWT/Auth Layer] --> API
  EMB[Embedding Manager] --> DB
  CHAT[RAG Chat Service Layer] --> EMB
  CHAT --> DB
  CHAT --> API

  WATCH[watchdog_worker + stats emitter] --> API
  WATCH --> MET[/metrics, /api/stats/]
```

## 2) Real-Time Alert Sequence

```mermaid
sequenceDiagram
  participant Cam as Camera
  participant Edge as edge_worker
  participant Bus as EventBus
  participant Ingest as stream_ingestion
  participant VLM as vlm_inference
  participant Decision as decision_engine
  participant Notify as notification/actuation
  participant WS as ws_handler
  participant UI as Dashboard
  participant PG as PostgreSQL

  Cam->>Edge: RTSP frames
  Edge->>Edge: detect + track + zone + dwell
  Edge->>Bus: raw_detections
  Ingest->>Bus: consume raw_detections
  Ingest->>Bus: alerts_ws (fast_alert)
  WS->>UI: fast_alert

  alt VLM required
    Ingest->>Bus: vlm_requests
    VLM->>Bus: consume vlm_requests
    VLM->>VLM: provider call + parse
    VLM->>Bus: vlm_results
    VLM->>Bus: alerts_ws (vlm_update)
    Decision->>Bus: consume vlm_results
    Decision->>PG: insert Detection/Incident
    Decision->>Bus: actions
    Notify->>Bus: consume actions
    Notify->>Notify: Slack/MQTT/Webhook/PTZ
  else no VLM
    Ingest->>Bus: actions (low severity path)
  end
```

## 3) Incident and Replay Sequence

```mermaid
sequenceDiagram
  participant UI as Incidents/Recordings UI
  participant API as FastAPI
  participant PG as PostgreSQL
  participant REC as recording_worker

  REC->>PG: write Segment metadata
  UI->>API: GET /api/incidents
  API->>PG: query Incident
  API-->>UI: incidents list

  UI->>API: GET /api/incidents/{id}/replay
  API->>PG: find incident + related segments
  API-->>UI: replay payload

  UI->>API: GET /api/recordings/{camera}/playlist
  API-->>UI: m3u8 playlist metadata
```

## 4) RAG/Chat Sequence (Planned Scope)

```mermaid
sequenceDiagram
  participant User as Analyst
  participant API as Chat API
  participant EMB as Embedding Manager
  participant VDB as Vector Search
  participant VLM as GenAI Provider
  participant PG as PostgreSQL

  User->>API: POST /api/chat/query
  API->>EMB: encode query
  EMB->>VDB: vector retrieval top-k
  API->>PG: fetch detections/incidents context
  API->>VLM: grounded generation request
  VLM-->>API: answer + cited IDs
  API-->>User: response + links/thumbnails
```

## 5) Implementation Critical Path

1. Mount `src/api/routes/*` into `src/api/server.py`.
2. Complete `recording_worker` DB write + segment linking path.
3. Implement auth in `src/auth/jwt_handler.py` and protect APIs.
4. Finish `zones`, `incidents`, `recordings` route handlers.
5. Complete observability path (`/api/stats`, `/metrics`) and watchdog automation.
