# ArgusV Configuration and Setup Guide

## 1) Runtime Profiles

### Profile A: Local App + Docker Infra (recommended dev)
- Start infra with `docker-compose.dev.yml`
- Run FastAPI app locally with `uvicorn`
- Host ports:
  - Postgres `localhost:5433`
  - Redis `localhost:6380`
  - MediaMTX RTSP `localhost:8554`, HLS `localhost:8888`

### Profile B: Full Docker Stack
- Start with `docker-compose.yml`
- App runs in container and uses service DNS (`postgres`, `redis`, `mediamtx`)
- Host app URL: `http://localhost:8000`

## 2) Environment Variables (Source of Truth)

Main config file: `src/config.py`.

### Infrastructure
- `POSTGRES_URL` (required)
- `REDIS_URL` (required)
- `MINIO_ENDPOINT` (optional in current monolith path)
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` (optional currently)

### Camera
- `CAMERA_ID`, `RTSP_URL` for single camera
- `CAMERAS` for multi-camera JSON:
```bash
CAMERAS='[{"id":"cam-01","rtsp_url":"rtsp://mediamtx:8554/cam-01"},{"id":"cam-02","rtsp_url":"rtsp://mediamtx:8554/cam-02"}]'
```

### Detection Pipeline
- `DETECT_FPS`
- `CONF_THRESHOLD`
- `YOLO_MODEL`
- `USE_MOTION_GATE`
- `MOTION_THRESHOLD`
- `USE_TRACKER`
- `LOITER_THRESHOLD_SEC`
- `TRACK_UPDATE_SEC`
- `TRACK_EVICT_SEC`
- `EMBED_FRAME`
- `FRAME_JPEG_Q`

### Recording
- `RECORDINGS_ENABLED`
- `SEGMENT_DURATION_SEC`
- `SEGMENT_TMP_DIR`
- `RECORDINGS_RETAIN_DAYS`
- Optional local output path: `LOCAL_RECORDINGS_DIR` (used by `recording_worker.py`)

### VLM/AI
- `OPENAI_API_KEY`
- `VLM_MODEL`
- `VLM_TRIAGE_MODEL`
- `USE_TIERED_VLM`
- `VLM_MAX_WORKERS`

### Notifications / Actuation
- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID`
- `RATE_LIMIT_TTL_SEC`
- `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`

### API
- `API_HOST`
- `API_PORT`
- `LOG_LEVEL`

## 3) .env Setup

Create `.env` from template:
```bash
cp .env.example .env
```

### Recommended `.env` for Profile A (local app + docker infra)
```env
POSTGRES_URL=postgresql://argus:password@localhost:5433/argus_db
REDIS_URL=redis://localhost:6380/0
CAMERA_ID=cam-01
RTSP_URL=rtsp://localhost:8554/cam-01
DETECT_FPS=5
CONF_THRESHOLD=0.45
USE_MOTION_GATE=true
LOITER_THRESHOLD_SEC=30
RECORDINGS_ENABLED=true
OPENAI_API_KEY=sk-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=#argus-alerts
LOG_LEVEL=INFO
```

### Recommended `.env` for Profile B (full docker)
```env
POSTGRES_URL=postgresql://argus:password@postgres:5432/argus_db
REDIS_URL=redis://redis:6379/0
CAMERA_ID=cam-01
RTSP_URL=rtsp://mediamtx:8554/cam-01
RECORDINGS_ENABLED=false
OPENAI_API_KEY=sk-...
LOG_LEVEL=INFO
```

## 4) Startup Commands

### Profile A
```bash
docker compose -f docker-compose.dev.yml up -d
uv venv .venv
source .venv/bin/activate
uv pip install -e .
alembic upgrade head
uvicorn main:app --host 0.0.0.0 --port 8000 --reload --app-dir src
```

### Profile B
```bash
# from repo parent where compose context resolves correctly
docker compose -f argusv/docker-compose.yml up --build
```

## 5) Camera and Stream Setup

Push a test stream:
```bash
ffmpeg -re -stream_loop -1 -i your_video.mp4 -c copy -f rtsp rtsp://localhost:8554/cam-01
```

HLS playback URL:
- `http://localhost:8888/cam-01/index.m3u8`

## 6) Database and Migrations

- Run migrations before app startup in local profile:
```bash
alembic upgrade head
```
- Models are in `src/db/models.py`
- Connection/session logic in `src/db/connection.py`

## 7) Configuration for MVP Demo

Use these minimum settings:
1. `OPENAI_API_KEY` set
2. `RECORDINGS_ENABLED=true`
3. `USE_TIERED_VLM=true`
4. `VLM_MAX_WORKERS=2` or `3`
5. `DETECT_FPS=5` and `CONF_THRESHOLD=0.45`
6. One stable RTSP stream in `CAMERAS` or `RTSP_URL`

## 8) Common Misconfiguration Issues

1. `POSTGRES_URL` points to `postgres` while running app locally
- Fix: use `localhost:5433`

2. `REDIS_URL` points to `redis` while running app locally
- Fix: use `localhost:6380`

3. Camera stream unavailable
- Check MediaMTX is running and RTSP URL path exists

4. No VLM results
- Check `OPENAI_API_KEY` and outbound network access

5. Recordings not linked to replay
- Ensure `RECORDINGS_ENABLED=true` and segment DB write tasks are complete

## 9) Security and Secret Handling

1. Never commit `.env`
2. Use separate keys for dev/staging/prod
3. Rotate API and bot tokens before investor demos
4. Prefer runtime secret injection in production

## 10) Productionization Checklist

1. Set strong DB credentials and private network policies
2. Enforce auth (`AUTH-01/02/06/07`) before public exposure
3. Enable centralized logs and metrics (`API-27`, `WATCH-08`)
4. Set retention policy (`REC-10`, `REC-11`, `DB-09`)
5. Load test WebSocket fanout and VLM concurrency

## 11) Configure Through API (No Restart for Runtime Settings)

Once app is running, you can manage most runtime setup via API:

1. Camera CRUD:
- `POST /api/cameras`, `PUT /api/cameras/{id}`, `DELETE /api/cameras/{id}`

2. Zone CRUD:
- `POST /api/zones/`, `PUT /api/zones/{id}`, `DELETE /api/zones/{id}`

3. Runtime tuning:
- `PUT /api/config/runtime` then `POST /api/config/apply`

4. Notification routing:
- `POST /api/notification-rules`

5. RAG/runtime key-value setup:
- `PUT /api/rag-config/{key}`

Example:
```bash
curl -X PUT http://localhost:8000/api/config/runtime \
  -H "Content-Type: application/json" \
  -d '{"detect_fps":5,"conf_threshold":0.45,"use_tiered_vlm":true,"recordings_enabled":true}'

curl -X POST http://localhost:8000/api/config/apply
```

Notes:
1. Boot-critical values like `POSTGRES_URL`, `REDIS_URL`, and API keys still come from environment at startup.
2. Runtime overrides are persisted in DB (`rag_configs` table, `group=runtime`).
