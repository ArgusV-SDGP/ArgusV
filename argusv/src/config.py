"""
config.py — Centralised configuration for the ArgusV monolith.
All env vars in one place instead of scattered across 8 services.
"""

import os
from pathlib import Path
from typing import Optional

# Load .env file for local development (no-op if file doesn't exist)
from dotenv import load_dotenv
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=False)


# ── Infrastructure ──────────────────────────────────────────────────────────── #

POSTGRES_URL   = os.getenv("POSTGRES_URL",   "postgresql://argus:password@postgres:5432/argus_db")
REDIS_URL      = os.getenv("REDIS_URL",      "redis://redis:6379/0")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_KEY      = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minioadmin")



# ── Cameras ─────────────────────────────────────────────────────────────────── #
# Simple single-camera fallback

CAMERA_ID  = os.getenv("CAMERA_ID",  "cam-01")
RTSP_URL   = os.getenv("RTSP_URL",   "rtsp://rtsp-simulator:8554/stream")

# Multi-camera: CAMERAS='[{"id":"cam-01","rtsp_url":"rtsp://..."},...]'

import json as _json
_cam_raw = os.getenv("CAMERAS")
CAMERAS: list[dict] = _json.loads(_cam_raw) if _cam_raw else [
    {"id": CAMERA_ID, "rtsp_url": RTSP_URL}
]


# ── Detection ───────────────────────────────────────────────────────────────── #

DETECT_FPS       = int(os.getenv("DETECT_FPS",       "5"))
CONF_THRESHOLD   = float(os.getenv("CONF_THRESHOLD", "0.45"))
YOLO_MODEL       = os.getenv("YOLO_MODEL",           "yolov8n.pt")
DETECT_CLASSES   = {0: "person", 2: "car", 7: "truck"}
USE_MOTION_GATE  = os.getenv("USE_MOTION_GATE",  "true").lower() == "true"
MOTION_THRESHOLD = float(os.getenv("MOTION_THRESHOLD", "0.003"))
USE_TRACKER      = os.getenv("USE_TRACKER",      "true").lower() == "true"
LOITER_SEC       = int(os.getenv("LOITER_THRESHOLD_SEC", "30"))
TRACK_UPDATE_SEC = int(os.getenv("TRACK_UPDATE_SEC",     "10"))
TRACK_EVICT_SEC  = int(os.getenv("TRACK_EVICT_SEC",      "5"))
EMBED_FRAME      = os.getenv("EMBED_FRAME",   "true").lower() == "true"
FRAME_JPEG_Q     = int(os.getenv("FRAME_JPEG_Q", "60"))



# ── Recording ───────────────────────────────────────────────────────────────── #

RECORDINGS_ENABLED   = os.getenv("RECORDINGS_ENABLED", "true").lower() == "true"
SEGMENT_DURATION_SEC = int(os.getenv("SEGMENT_DURATION_SEC", "10"))
SEGMENT_TMP_DIR      = os.getenv("SEGMENT_TMP_DIR", "./tmp/argus_segments")
RECORDINGS_RETAIN_DAYS = int(os.getenv("RECORDINGS_RETAIN_DAYS", "7"))
LOCAL_RECORDINGS_DIR   = os.getenv("LOCAL_RECORDINGS_DIR", "./recordings")



# ── VLM / GenAI ─────────────────────────────────────────────────────────────── #

# Active provider: openai | gemini | ollama | llamacpp | disabled
GENAI_PROVIDER    = os.getenv("GENAI_PROVIDER",   "openai")

# OpenAI
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
VLM_MODEL         = os.getenv("VLM_MODEL",        "gpt-4o")
VLM_TRIAGE_MODEL  = os.getenv("VLM_TRIAGE_MODEL", "gpt-4o-mini")
USE_TIERED_VLM    = os.getenv("USE_TIERED_VLM", "true").lower() == "true"
VLM_MAX_WORKERS   = int(os.getenv("VLM_MAX_WORKERS", "3"))

# Gemini
GEMINI_API_KEY          = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL            = os.getenv("GEMINI_MODEL",        "gemini-2.0-flash")
GEMINI_VISION_MODEL     = os.getenv("GEMINI_VISION_MODEL", "gemini-1.5-pro")

# Ollama (local)
OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL",    "llava")   # vision-capable model

# LlamaCpp (OpenAI-compatible server)
LLAMACPP_BASE_URL = os.getenv("LLAMACPP_BASE_URL", "http://llamacpp:8080")
LLAMACPP_MODEL    = os.getenv("LLAMACPP_MODEL",    "llava")



# ── Notifications ───────────────────────────────────────────────────────────── #

SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN",  "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "#argus-alerts")
RATE_LIMIT_TTL   = int(os.getenv("RATE_LIMIT_TTL_SEC", "300"))



# ── MQTT / Actuation ────────────────────────────────────────────────────────── #

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")




# ── API Server ──────────────────────────────────────────────────────────────── #

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")



# ── Authentication / Authorization ─────────────────────────────────────────── #

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
JWT_REFRESH_EXPIRE_MINUTES = int(os.getenv("JWT_REFRESH_EXPIRE_MINUTES", "1440"))

AUTH_USERS_JSON = os.getenv(
    "AUTH_USERS_JSON",
    '{"admin":{"password":"admin123","role":"ADMIN"},"operator":{"password":"operator123","role":"OPERATOR"}}',
)
API_KEYS_JSON = os.getenv(
    "API_KEYS_JSON",
    '{"local-dev-api-key":{"subject":"service-client","role":"SERVICE"}}',
)

PROXY_AUTH_ENABLED = os.getenv("PROXY_AUTH_ENABLED", "false").lower() == "true"
PROXY_AUTH_USER_HEADER = os.getenv("PROXY_AUTH_USER_HEADER", "x-forwarded-user")
PROXY_AUTH_ROLE_HEADER = os.getenv("PROXY_AUTH_ROLE_HEADER", "x-forwarded-role")

try:
    AUTH_USERS = _json.loads(AUTH_USERS_JSON) if AUTH_USERS_JSON else {}
    if not isinstance(AUTH_USERS, dict):
        AUTH_USERS = {}
except Exception:
    AUTH_USERS = {}

try:
    API_KEYS = _json.loads(API_KEYS_JSON) if API_KEYS_JSON else {}
    if not isinstance(API_KEYS, dict):
        API_KEYS = {}
except Exception:
    API_KEYS = {}
