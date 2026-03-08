# ArgusV API Documentation (Current + Remaining Scope)

Base URL: `http://localhost:8000`

## Status Legend
- `implemented`: available now
- `planned`: still backlog

## 1) Implemented Endpoints

### Core
- `GET /`
- `WS /ws/alerts`
- `GET /health`

### Authentication
- `POST /auth/register`
- `POST /auth/token`
- `POST /auth/refresh`
- `GET /auth/me`

### Authorization Roles (RBAC)
- `ADMIN`: full access (all CRUD + config)
- `OPERATOR`: operational CRUD (cameras/zones/incidents), read replay data
- `VIEWER`: read-only access to incidents/recordings/cameras/zones
- `SERVICE`: API-key/service access for config/runtime endpoints
- `POST /auth/register` creates `VIEWER` accounts by default

### Cameras
- `GET /api/cameras`
- `GET /api/cameras/{camera_id}`
- `POST /api/cameras`
- `PUT /api/cameras/{camera_id}`
- `DELETE /api/cameras/{camera_id}`

### Incidents
- `GET /api/incidents`
- `GET /api/incidents/{incident_id}`
- `PATCH /api/incidents/{incident_id}`
- `GET /api/incidents/{incident_id}/replay`

### Detections
- `GET /api/detections`

### Zones
- `GET /api/zones`
- `GET /api/zones/{zone_id}`
- `POST /api/zones`
- `PUT /api/zones/{zone_id}`
- `PATCH /api/zones/{zone_id}`
- `DELETE /api/zones/{zone_id}`

### Recordings
- `GET /api/recordings/{camera_id}`
- `GET /api/recordings/{camera_id}/playlist`
- `GET /api/recordings/{camera_id}/timeline`

### Runtime Configuration
- `GET /api/config/runtime`
- `PUT /api/config/runtime`
- `POST /api/config/apply`

### Notification Rules
- `GET /api/notification-rules`
- `POST /api/notification-rules`
- `PUT /api/notification-rules/{rule_id}`
- `DELETE /api/notification-rules/{rule_id}`

### RAG Config KV
- `GET /api/rag-config?group=rag`
- `PUT /api/rag-config/{key}`
- `DELETE /api/rag-config/{key}?group=rag`

## 2) API-Based Setup Flow (Recommended)

1. Create camera
```bash
ACCESS_TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/api/cameras \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"camera_id":"cam-01","name":"Front Gate","rtsp_url":"rtsp://mediamtx:8554/cam-01","fps":5}'
```

2. Create zone
```bash
curl -X POST http://localhost:8000/api/zones \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Gate","polygon_coords":[[0.10,0.20],[0.45,0.20],[0.45,0.80]],"zone_type":"security","dwell_threshold_sec":30,"active":true}'
```

3. Update runtime parameters
```bash
curl -X PUT http://localhost:8000/api/config/runtime \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"conf_threshold":0.45,"detect_fps":5,"use_motion_gate":true,"use_tiered_vlm":true,"recordings_enabled":true}'
```

4. Apply runtime config to workers
```bash
curl -X POST http://localhost:8000/api/config/apply \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

5. Add notification rule
```bash
curl -X POST http://localhost:8000/api/notification-rules \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"zone_id":"global","severity":"HIGH","channels":["slack"],"config":{"channel":"#argus-alerts"}}'
```

## 3) Key Payload Contracts

### `POST /auth/token`
```json
{
  "username": "admin",
  "password": "admin123"
}
```

### `POST /auth/refresh`
```json
{
  "refresh_token": "<refresh-token>"
}
```

### `POST /auth/register`
```json
{
  "username": "newuser",
  "password": "StrongPass123"
}
```

### `X-API-Key` header auth (service-to-service)
```bash
curl -X GET http://localhost:8000/api/config/runtime \
  -H "X-API-Key: local-dev-api-key"
```

### `POST /api/cameras`
```json
{
  "camera_id": "cam-01",
  "name": "Front Gate",
  "rtsp_url": "rtsp://mediamtx:8554/cam-01",
  "zone_id": null,
  "status": "online",
  "resolution": "1920x1080",
  "fps": 5
}
```

### `POST /api/zones`
```json
{
  "name": "Gate",
  "polygon_coords": [[0.1, 0.2], [0.45, 0.2], [0.45, 0.8]],
  "zone_type": "security",
  "dwell_threshold_sec": 30,
  "active": true
}
```

### `PATCH /api/incidents/{incident_id}`
```json
{
  "status": "RESOLVED",
  "annotation": "Verified by operator"
}
```

### `PUT /api/config/runtime`
```json
{
  "detect_fps": 5,
  "conf_threshold": 0.45,
  "use_motion_gate": true,
  "recordings_enabled": true,
  "use_tiered_vlm": true,
  "vlm_max_workers": 3
}
```

## 4) Planned Endpoints (Not Yet Implemented)

- `GET /api/stats` (`API-27`)
- `GET /metrics` (`WATCH-08`)
- additional auth hardening (password hashing, token revocation store, audit trails)
- `POST /api/chat/query`, `WS /ws/chat`, semantic search endpoints (`VLM-05+`)

## 5) Error Model

- `400` bad input / invalid IDs
- `404` resource not found
- `409` logical conflict (for example replay without camera/timestamp)
- `422` validation failure
- `500` internal server errors
- `401` missing/invalid JWT or API key
- `403` authenticated but role not allowed
