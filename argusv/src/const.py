"""
const.py — System-wide constants
----------------------------------
Frigate equivalent: frigate/const.py

All magic strings, limits, and enums in one place.
"""

# ── Event types ──────────────────────────────────────────────────
EVENT_START     = "START"
EVENT_UPDATE    = "UPDATE"
EVENT_LOITERING = "LOITERING"
EVENT_END       = "END"

# ── Threat levels ─────────────────────────────────────────────────
THREAT_HIGH   = "HIGH"
THREAT_MEDIUM = "MEDIUM"
THREAT_LOW    = "LOW"
THREAT_UNKNOWN = "UNKNOWN"

# ── Review / Incident status ──────────────────────────────────────
STATUS_OPEN     = "OPEN"
STATUS_RESOLVED = "RESOLVED"
STATUS_PENDING  = "pending"
STATUS_REVIEWED = "reviewed"
STATUS_DISMISSED= "dismissed"

# ── Object classes (COCO labelmap subset) ────────────────────────
CLASS_PERSON   = "person"
CLASS_CAR      = "car"
CLASS_TRUCK    = "truck"
CLASS_BICYCLE  = "bicycle"
CLASS_MOTORBIKE= "motorcycle"
CLASS_BUS      = "bus"

ALL_CLASSES = [CLASS_PERSON, CLASS_CAR, CLASS_TRUCK,
               CLASS_BICYCLE, CLASS_MOTORBIKE, CLASS_BUS]

# ── Bus queue names ───────────────────────────────────────────────
QUEUE_RAW_DETECTIONS = "raw_detections"
QUEUE_VLM_REQUESTS   = "vlm_requests"
QUEUE_VLM_RESULTS    = "vlm_results"
QUEUE_ACTIONS        = "actions"
QUEUE_ALERTS_WS      = "alerts_ws"
QUEUE_SEGMENTS       = "segments"

# ── Redis key prefixes ────────────────────────────────────────────
REDIS_CAMERA_STATUS  = "camera:status:"          # + camera_id (TTL=30s)
REDIS_ZONE_CONFIG    = "config:zone:"             # + zone_id
REDIS_ZONE_CHANNEL   = "argus:zone:updated"       # pubsub channel
REDIS_ALERT_COOLDOWN = "alert:cooldown:"          # + zone_id:severity

# ── Recording ─────────────────────────────────────────────────────
DEFAULT_SEGMENT_DURATION_SEC = 10
DEFAULT_RETAIN_DAYS          = 30

# ── Queue sizes (backpressure limits) ────────────────────────────
QUEUE_RAW_MAXSIZE  = 1000
QUEUE_VLM_MAXSIZE  = 200
QUEUE_RES_MAXSIZE  = 200
QUEUE_ACT_MAXSIZE  = 500
QUEUE_WS_MAXSIZE   = 2000

# ── Detection ─────────────────────────────────────────────────────
DEFAULT_CONF_THRESHOLD = 0.45
DEFAULT_DETECT_FPS     = 5
DEFAULT_LOITER_SEC     = 30

# ── API ───────────────────────────────────────────────────────────
API_DEFAULT_LIMIT = 50
API_MAX_LIMIT     = 500
