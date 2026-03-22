"""
config.py — Centralised configuration for the ArgusV monolith.
All env vars in one place instead of scattered across 8 services.

BRAYAN NOTE: Key env vars already present:
  Infrastructure: POSTGRES_URL, REDIS_URL, MINIO_*
  Cameras:        CAMERA_ID, RTSP_URL, CAMERAS (multi-cam JSON)
  Detection:      DETECT_FPS, CONF_THRESHOLD, YOLO_MODEL, LOITER_SEC, USE_MOTION_GATE
  Recording:      RECORDINGS_ENABLED, SEGMENT_DURATION_SEC, RECORDINGS_RETAIN_DAYS (7 days)
  VLM:            OPENAI_API_KEY, VLM_MODEL (gpt-4o), USE_TIERED_VLM, VLM_MAX_WORKERS
  Notifications:  SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, RATE_LIMIT_TTL
  API:            API_HOST, API_PORT, LOG_LEVEL
Vars I need to add later: INCIDENT_AUTO_RESOLVE_MINUTES, STATIONARY_N/M_FRAMES, ENABLE_DET06
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
# Multi-camera: CAMERAS='[{"id":"cam-01","name":"Front Door","rtsp_url":"rtsp://..."},...]'
# Falls back to single-camera env vars if CAMERAS is not set.

CAMERA_ID  = os.getenv("CAMERA_ID",  "cam-01")
RTSP_URL   = os.getenv("RTSP_URL",   "rtsp://localhost:8554/cam-01")

# RTSP host used in camera URLs — "mediamtx" inside Docker, "localhost" for local dev
RTSP_HOST  = os.getenv("RTSP_HOST", "localhost")

import json as _json
_cam_raw = os.getenv("CAMERAS")
if _cam_raw:
    CAMERAS: list[dict] = _json.loads(_cam_raw)
else:
    # Default demo cameras — each gets an RTSP sim in docker-compose.dev.yml
    CAMERAS: list[dict] = [
        {"id": "cam-01", "name": "Front Door",  "rtsp_url": f"rtsp://{RTSP_HOST}:8554/cam-01"},
        {"id": "cam-02", "name": "Parking Lot",  "rtsp_url": f"rtsp://{RTSP_HOST}:8554/cam-02"},
    ]


# ── Detection ───────────────────────────────────────────────────────────────── #

DETECT_FPS       = int(os.getenv("DETECT_FPS",       "5"))
CONF_THRESHOLD   = float(os.getenv("CONF_THRESHOLD", "0.45"))
YOLO_MODEL       = os.getenv("YOLO_MODEL",           "yolo11m.pt")
USE_MOTION_GATE  = os.getenv("USE_MOTION_GATE",  "true").lower() == "true"
MOTION_THRESHOLD = float(os.getenv("MOTION_THRESHOLD", "0.003"))
USE_TRACKER      = os.getenv("USE_TRACKER",      "true").lower() == "true"
LOITER_SEC       = int(os.getenv("LOITER_THRESHOLD_SEC", "30"))
TRACK_UPDATE_SEC = int(os.getenv("TRACK_UPDATE_SEC",     "10"))
TRACK_EVICT_SEC  = int(os.getenv("TRACK_EVICT_SEC",      "5"))
ZONE_RESYNC_SEC  = int(os.getenv("ZONE_RESYNC_SEC",      "60"))
EMBED_FRAME      = os.getenv("EMBED_FRAME",   "true").lower() == "true"
FRAME_JPEG_Q     = int(os.getenv("FRAME_JPEG_Q", "60"))

# ── COCO-80 class map ────────────────────────────────────────────────────────
# Full lookup table for both validation and shorthand resolution.
# DETECT_CLASSES is the active filter — only these class IDs are forwarded.
COCO_CLASS_MAP: dict[int, str] = {
    0:"person",       1:"bicycle",      2:"car",          3:"motorcycle",
    4:"airplane",     5:"bus",          6:"train",        7:"truck",
    8:"boat",         9:"traffic light",10:"fire hydrant",11:"stop sign",
    12:"parking meter",13:"bench",      14:"bird",        15:"cat",
    16:"dog",         17:"horse",       18:"sheep",       19:"cow",
    20:"elephant",    21:"bear",        22:"zebra",       23:"giraffe",
    24:"backpack",    25:"umbrella",    26:"handbag",     27:"tie",
    28:"suitcase",    29:"frisbee",     30:"skis",        31:"snowboard",
    32:"sports ball", 33:"kite",        34:"baseball bat",35:"baseball glove",
    36:"skateboard",  37:"surfboard",   38:"tennis racket",39:"bottle",
    40:"wine glass",  41:"cup",         42:"fork",        43:"knife",
    44:"spoon",       45:"bowl",        46:"banana",      47:"apple",
    48:"sandwich",    49:"orange",      50:"broccoli",    51:"carrot",
    52:"hot dog",     53:"pizza",       54:"donut",       55:"cake",
    56:"chair",       57:"couch",       58:"potted plant",59:"bed",
    60:"dining table",61:"toilet",      62:"tv",          63:"laptop",
    64:"mouse",       65:"remote",      66:"keyboard",    67:"cell phone",
    68:"microwave",   69:"oven",        70:"toaster",     71:"sink",
    72:"refrigerator",73:"book",        74:"clock",       75:"vase",
    76:"scissors",    77:"teddy bear",  78:"hair drier",  79:"toothbrush",
}
COCO_NAME_TO_ID: dict[str, int] = {v: k for k, v in COCO_CLASS_MAP.items()}


def _parse_detect_classes() -> dict[int, str]:
    """
    Parse DETECT_CLASSES from env.

    Pattern A (explicit ID:label pairs — takes precedence):
      DETECT_CLASSES=0:person,2:car,7:truck,5:bus

    Pattern B (label names resolved via COCO_CLASS_MAP):
      DETECT_CLASS_NAMES=person,car,truck,bus

    Falls back to person+car+truck if neither is set.
    """
    explicit = os.getenv("DETECT_CLASSES", "")
    if explicit:
        result: dict[int, str] = {}
        for pair in explicit.split(","):
            pair = pair.strip()
            if ":" in pair:
                id_str, label = pair.split(":", 1)
                try:
                    result[int(id_str.strip())] = label.strip()
                except ValueError:
                    pass
        if result:
            return result

    names = os.getenv("DETECT_CLASS_NAMES", "")
    if names:
        result = {}
        for name in names.split(","):
            name = name.strip().lower()
            if name in COCO_NAME_TO_ID:
                result[COCO_NAME_TO_ID[name]] = name
        if result:
            return result

    return {0: "person", 2: "car", 7: "truck"}


DETECT_CLASSES: dict[int, str] = _parse_detect_classes()



# ── Recording ───────────────────────────────────────────────────────────────── #

RECORDINGS_ENABLED   = os.getenv("RECORDINGS_ENABLED", "true").lower() == "true"
SEGMENT_DURATION_SEC = int(os.getenv("SEGMENT_DURATION_SEC", "10"))
SEGMENT_TMP_DIR      = os.getenv("SEGMENT_TMP_DIR", "./tmp/argus_segments")
RECORDINGS_RETAIN_DAYS = int(os.getenv("RECORDINGS_RETAIN_DAYS", "7"))
LOCAL_RECORDINGS_DIR   = os.getenv("LOCAL_RECORDINGS_DIR", "./recordings")

# Watchdog
WATCHDOG_DISK_WARN_PCT = int(os.getenv("WATCHDOG_DISK_WARN_PCT", "80"))



# ── VLM / GenAI ─────────────────────────────────────────────────────────────── #

# Active provider: openai | gemini | ollama | llamacpp | disabled
GENAI_PROVIDER    = os.getenv("GENAI_PROVIDER",   "openai")

# OpenAI
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
VLM_MODEL           = os.getenv("VLM_MODEL",           "gpt-4o")
EMBEDDING_MODEL     = os.getenv("EMBEDDING_MODEL",     "text-embedding-3-small")
VLM_TRIAGE_MODEL  = os.getenv("VLM_TRIAGE_MODEL", "gpt-4o-mini")
USE_TIERED_VLM    = os.getenv("USE_TIERED_VLM", "true").lower() == "true"
VLM_MAX_WORKERS   = int(os.getenv("VLM_MAX_WORKERS", "3"))

# Gemini
GEMINI_API_KEY          = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL            = os.getenv("GEMINI_MODEL",        "gemini-2.0-flash")
GEMINI_VISION_MODEL     = os.getenv("GEMINI_VISION_MODEL", "gemini-1.5-pro")

# Segment VLM provider — who describes completed video segments for RAG indexing
# "gemini"  → uploads full .ts video to Gemini File API (native video understanding)
# "openai"  → extracts FRAMES_PER_SEGMENT frames and sends to GPT-4o (default)
SEGMENT_VLM_PROVIDER    = os.getenv("SEGMENT_VLM_PROVIDER", "openai")

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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")


# ── MQTT / Actuation ────────────────────────────────────────────────────────── #

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")




# ── API Server ──────────────────────────────────────────────────────────────── #

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
MEDIAMTX_HLS_BASE = os.getenv("MEDIAMTX_HLS_BASE", "http://localhost:8888")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]



# ── Authentication / Authorization ─────────────────────────────────────────── #

DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
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

# ── Development Auth Bypass ──────────────────────────────────────────────────
# When true, ALL requests are treated as ADMIN — never use in production!
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"

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
