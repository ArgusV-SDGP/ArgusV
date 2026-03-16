# 🔭 ArgusV — Development Setup Guide

ArgusV is a monolithic AI-powered security camera monitoring system. It runs **4 containers** (ArgusV app, PostgreSQL, Redis, MediaMTX) instead of the legacy 13-service architecture.

---

## 📋 Prerequisites

Make sure the following are installed on your machine before you begin:

| Tool | Version | Notes |
|---|---|---|
| **Python** | 3.11+ | Required for local dev server |
| **Docker Desktop** | Latest | Includes Docker Compose v2 |
| **uv** | Latest | Fast Python package manager |
| **Git** | Any | Version control |

> **Windows Users:** Ensure Docker Desktop is running with WSL 2 backend enabled.

---

## 🗂️ Project Structure

```
argusv/
├── src/                    # All Python source code
│   ├── main.py             # FastAPI app entry point
│   ├── config.py           # Centralised env var config
│   ├── api/                # REST API routes & server
│   ├── workers/            # Background pipeline workers
│   ├── db/                 # SQLAlchemy models & session
│   ├── genai/              # VLM / OpenAI integration
│   ├── comms/              # Slack notifications
│   ├── events/             # Event bus logic
│   ├── auth/               # Authentication
│   └── ...
├── alembic/                # DB migration scripts
├── static/                 # Frontend dashboard assets
├── tests/                  # Pytest test suite
├── docker-compose.dev.yml  # Infrastructure only (local dev)
├── docker-compose.yml      # Full production stack
├── Dockerfile              # App container image
├── pyproject.toml          # Python dependencies
├── alembic.ini             # Alembic DB migration config
└── .env.example            # Environment variable template
```

---

## ⚙️ Environment Setup

### 1. Clone & Navigate

```bash
git clone <your-repo-url>
cd argusv
```

### 2. Create Your `.env` File

Copy the example and fill in your secrets:

```bash
cp .env.example .env
```

Open `.env` and update the values:

```env
# ── VLM ──────────────────────────────────────────────
OPENAI_API_KEY=sk-...          # Required for AI analysis

# ── Notifications ─────────────────────────────────────
SLACK_BOT_TOKEN=xoxb-...       # Optional: Slack alerts
SLACK_CHANNEL_ID=#argus-alerts

# ── Camera ────────────────────────────────────────────
CAMERA_ID=cam-01
RTSP_URL=rtsp://mediamtx:8554/cam-01   # Points to local MediaMTX

# ── Detection ─────────────────────────────────────────
DETECT_FPS=5
CONF_THRESHOLD=0.45
USE_MOTION_GATE=true
LOITER_THRESHOLD_SEC=30

# ── Recording ─────────────────────────────────────────
RECORDINGS_ENABLED=false

# ── DB / Redis (defaults match docker-compose.dev.yml) ─
POSTGRES_URL=postgresql://argus:password@localhost:5434/argus_db
REDIS_URL=redis://localhost:6380/0
```

> ⚠️ **Never commit `.env` to Git.** It is already in `.gitignore`.

---

## 🐳 Option A — Local Development (Recommended)

This is the fast inner-loop workflow. Infrastructure (Postgres, Redis, MediaMTX) runs in Docker, but the Python app runs **locally** with hot-reload.

### Step 1 — Start Infrastructure Containers

```bash
docker compose -f docker-compose.dev.yml up -d
```

This starts:
- **PostgreSQL** → `localhost:5434`
- **Redis** → `localhost:6380`
- **MediaMTX** (RTSP/HLS broker) → RTSP `localhost:8554`, HLS `localhost:8888`

Verify containers are healthy:

```bash
docker compose -f docker-compose.dev.yml ps
```

### Step 2 — Install Python Dependencies

Install `uv` if you haven't already:

```bash
pip install uv
```

Create a virtual environment and install all dependencies:

```bash
uv venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

uv pip install -e .
```

### Step 3 — Run Database Migrations

Apply the Alembic schema migrations against the dev Postgres:

```bash
alembic upgrade head
```

> `alembic.ini` is pre-configured to connect to `localhost:5434/argus_db` (the dev container port).

### Step 4 — Start the App Server

```bash
# From the argusv/ directory
uvicorn main:app --host 1270.1--port 6969 --app-dir src
```

The app will be available at:
- **Dashboard:** http://localhost:8000
- **API Docs (Swagger):** http://localhost:8000/docs
- **API Docs (Redoc):** http://localhost:8000/redoc

---

## 🐋 Option B — Full Docker Stack

Runs **everything** (app + infrastructure) in Docker. Use this to test the production build locally.

### Build & Run

```bash
# Must be run from the parent directory (ARGUSV_REVAMPED/)
cd ..
docker compose -f argusv/docker-compose.yml up --build
```

> The `Dockerfile` build context is set to the **parent directory** (`..`) because it also copies `edge-gateway/src` as a vendor module.

Services exposed:
| Service | Host Port | Description |
|---|---|---|
| ArgusV API | `8000` | Dashboard + REST API |
| PostgreSQL | `5434` | Avoids conflict with local Postgres |
| Redis | `6380` | Avoids conflict with local Redis |
| MediaMTX RTSP | `8554` | RTSP camera input |
| MediaMTX HLS | `8888` | HLS stream output for dashboard |
| MediaMTX WebRTC | `8889` | WebRTC output |

---

## 🎥 Connecting a Camera

### Option 1: Real IP Camera
Point your camera's RTSP stream output to:
```
rtsp://localhost:8554/cam-01
```

Update `.env`:
```env
RTSP_URL=rtsp://mediamtx:8554/cam-01
```

### Option 2: Test Stream with FFmpeg
Push a test video file as a fake RTSP stream:

```bash
ffmpeg -re -stream_loop -1 -i your_video.mp4 \
  -c copy -f rtsp rtsp://localhost:8554/cam-01
```

### HLS Playback URL (for dashboard)
```
http://localhost:8888/cam-01/index.m3u8
```

---

## 🗃️ Database Migrations (Alembic)

All database schema changes are managed via Alembic.

```bash
# Apply all pending migrations
alembic upgrade head

# Check for multiple heads before creating a revision
alembic heads

# Create a new migration after editing models
alembic revision --autogenerate -m "describe your change"

# Roll back one migration
alembic downgrade -1

# Check current migration status
alembic current
```

If `alembic heads` shows more than one head, merge them before creating new revisions:

```bash
alembic merge -m "merge heads" <head_1> <head_2>
alembic upgrade head
```

> **Dev Note:** `alembic.ini` points to `localhost:5434` (dev compose port). In Docker, the app uses the internal `postgres:5432` address from the `POSTGRES_URL` env var.

---

## 🧪 Running Tests

```bash
# Make sure the dev infra is running first
docker compose -f docker-compose.dev.yml up -d

# Run the full test suite
pytest tests/ -v

# Run a specific test file
pytest tests/test_api.py -v
```

---

## 🔑 Key Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(required)* | OpenAI key for VLM analysis |
| `POSTGRES_URL` | `postgresql://argus:password@postgres:5432/argus_db` | PostgreSQL connection string |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection string |
| `CAMERA_ID` | `cam-01` | Camera identifier |
| `RTSP_URL` | `rtsp://mediamtx:8554/cam-01` | Camera RTSP source URL |
| `DETECT_FPS` | `5` | Frames per second to analyze |
| `CONF_THRESHOLD` | `0.45` | YOLO detection confidence threshold |
| `YOLO_MODEL` | `yolov8n.pt` | YOLOv8 model file name |
| `USE_MOTION_GATE` | `true` | Skip detection when no motion |
| `MOTION_THRESHOLD` | `0.003` | Motion sensitivity (0–1) |
| `USE_TRACKER` | `true` | Enable ByteTrack object tracking |
| `LOITER_THRESHOLD_SEC` | `30` | Seconds before loitering alert |
| `RECORDINGS_ENABLED` | `false` | Enable video clip recording |
| `SEGMENT_TMP_DIR` | `/tmp/argus_segments` | Temp dir for video segments |
| `SLACK_BOT_TOKEN` | *(optional)* | Slack bot token for alerts |
| `SLACK_CHANNEL_ID` | `#argus-alerts` | Slack channel for notifications |
| `VLM_MODEL` | `gpt-4o` | Primary VLM model |
| `VLM_TRIAGE_MODEL` | `gpt-4o-mini` | Fast triage VLM model |
| `USE_TIERED_VLM` | `true` | Use fast model first, escalate |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## 🔧 Multi-Camera Setup

To monitor multiple cameras simultaneously, set the `CAMERAS` env var as a JSON array:

```env
CAMERAS=[{"id":"cam-01","rtsp_url":"rtsp://mediamtx:8554/cam-01"},{"id":"cam-02","rtsp_url":"rtsp://192.168.1.101:554/stream"}]
```

When `CAMERAS` is set, the single `CAMERA_ID` / `RTSP_URL` variables are ignored.

---

## 🛠️ Common Troubleshooting

### Port conflicts
The dev compose uses non-standard host ports to avoid conflicts:
- Postgres binds to `5434` (not `5432`)
- Redis binds to `6380` (not `6379`)

### WSL 2 DNS issues on Windows
The production `Dockerfile` uses `network: host` during the `apt-get` step to fix a known WSL2 DNS resolution bug. This is expected.

### YOLO model not found
The `yolov8n.pt` model file is bundled in `src/`. Ultralytics will also auto-download it on first run if missing.

### Database connection refused
Make sure the dev containers are running and healthy before starting the app:
```bash
docker compose -f docker-compose.dev.yml ps
```

### Alembic can't connect
Ensure your `.env` has the correct `POSTGRES_URL` pointing to `localhost:5434` for local dev:
```env
POSTGRES_URL=postgresql://argus:password@localhost:5434/argus_db
```

### pgvector extension not available
If migrations fail with `extension "vector" is not available`, your running Postgres container was created from a non-pgvector image.
Recreate the dev DB container with the pgvector-enabled image:
```bash
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d
alembic upgrade head
```

---

## 📦 Tech Stack Summary

| Layer | Technology |
|---|---|
| **API Framework** | FastAPI + Uvicorn |
| **Computer Vision** | YOLOv8 (Ultralytics) + ByteTrack |
| **Video Processing** | OpenCV (headless) + FFmpeg |
| **Zone Matching** | Shapely |
| **Database** | PostgreSQL 15 + SQLAlchemy + Alembic |
| **Cache / Pub-Sub** | Redis 7 |
| **AI Analysis** | OpenAI GPT-4o / GPT-4o-mini |
| **Notifications** | Slack Bot API |
| **Stream Broker** | MediaMTX (RTSP → HLS/WebRTC) |
| **Containerization** | Docker + Docker Compose |
| **Package Manager** | uv |
