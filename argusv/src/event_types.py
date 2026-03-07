"""
types.py — Shared type definitions
------------------------------------
Frigate equivalent: frigate/types.py

TypedDicts and dataclasses for type-safe
event passing between workers.
"""

from typing import TypedDict, Optional
from datetime import datetime


class DetectionEvent(TypedDict):
    """Emitted by edge_worker, consumed by pipeline_worker."""
    event_id:            str
    event_type:          str        # START | UPDATE | LOITERING | END
    camera_id:           str
    timestamp:           float      # UTC epoch
    object_class:        str
    confidence:          float
    track_id:            int
    zone_id:             Optional[str]
    zone_name:           Optional[str]
    dwell_sec:           float
    bbox:                dict       # {x1, y1, x2, y2} pixel coords
    trigger_frame_b64:   Optional[str]  # base64 JPEG (only on START/LOITERING)
    status:              str        # "analyzing" | "complete"


class VLMResult(TypedDict):
    """Returned by OpenAI/Gemini/Ollama analysis."""
    threat_level:        str        # HIGH | MEDIUM | LOW | UNKNOWN
    is_threat:           bool
    summary:             str
    recommended_action:  Optional[str]  # ALERT | MONITOR | IGNORE


class AlertAction(TypedDict):
    """Emitted by decision_engine_worker, consumed by notification_worker."""
    action_type:         str        # ALERT | LOG | IGNORE
    camera_id:           str
    zone_name:           Optional[str]
    object_class:        str
    threat_level:        str
    summary:             Optional[str]
    timestamp:           float
    event_id:            str


class SegmentEvent(TypedDict):
    """Emitted by recording_worker when segment is complete."""
    type:                str        # "segment_registered"
    camera_id:           str
    local_path:          str
    start_time:          str        # ISO8601
    end_time:            str
    size_bytes:          int
