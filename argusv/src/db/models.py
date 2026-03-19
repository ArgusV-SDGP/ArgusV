"""
db/models.py — ArgusV SQLAlchemy models (monolith source of truth)
-------------------------------------------------------------------
Moved from decision-engine/src/models.py.
The old decision-engine/src/models.py can now be deleted.

Tables:
  cameras           edge-gateway domain   (edge-gateway writes, others read)
  segments          edge-gateway domain
  zones             decision-engine domain
  rules             decision-engine domain
  incidents         decision-engine domain
  detections        decision-engine domain
  notification_rules decision-engine domain
  rag_configs       decision-engine domain
"""

from sqlalchemy import (
    Column, String, Boolean, Float, DateTime,
    ForeignKey, Integer, Text, BigInteger, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import uuid

Base = declarative_base()


# ── Edge-gateway domain ──────────────────────────────────────────────────────

class Camera(Base):
    __tablename__ = "cameras"

    camera_id  = Column(String, primary_key=True)   # e.g. "cam-01"
    name       = Column(String, nullable=False)
    rtsp_url   = Column(String)
    zone_id    = Column(UUID(as_uuid=True), ForeignKey("zones.zone_id"), nullable=True)
    status     = Column(String, default="online")   # online | offline | disabled
    resolution = Column(String)
    fps        = Column(Integer, default=25)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen  = Column(DateTime, default=datetime.utcnow)

    segments   = relationship("Segment",   back_populates="camera_rel", lazy="dynamic")
    detections = relationship("Detection", back_populates="camera_rel", lazy="dynamic")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        Index("ix_segments_camera_start", "camera_id", "start_time"),
        Index("ix_segments_start",        "start_time"),
    )

    segment_id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id       = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    start_time      = Column(DateTime, nullable=False, index=True)
    end_time        = Column(DateTime, nullable=False)
    duration_sec    = Column(Float, nullable=False)
    minio_path      = Column(String, nullable=False)
    size_bytes      = Column(BigInteger, default=0)
    has_motion      = Column(Boolean, default=False)
    has_detections  = Column(Boolean, default=False)
    detection_count = Column(Integer, default=0)
    retain_until    = Column(DateTime, nullable=True)
    locked          = Column(Boolean, default=False)

    camera_rel = relationship("Camera",    back_populates="segments")
    detections = relationship("Detection", back_populates="segment", lazy="dynamic")


# ── Decision-engine domain ────────────────────────────────────────────────────

class Zone(Base):
    __tablename__ = "zones"

    zone_id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name                = Column(String, nullable=False)
    polygon_coords      = Column(JSONB, nullable=False)  # [[x,y], ...]  normalised 0..1
    zone_type           = Column(String, default="security")
    dwell_threshold_sec = Column(Integer, default=30)
    active              = Column(Boolean, default=True)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, nullable=True)


class Rule(Base):
    __tablename__ = "rules"

    rule_id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id       = Column(UUID(as_uuid=True), ForeignKey("zones.zone_id"))
    trigger_type  = Column(String)             # "loitering", "intrusion", etc.
    severity      = Column(String)             # "HIGH", "MEDIUM", "LOW"
    action_config = Column(JSONB)              # {"siren_duration": 30}
    is_active     = Column(Boolean, default=True)


class Incident(Base):
    __tablename__ = "incidents"

    incident_id  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id    = Column(String, ForeignKey("cameras.camera_id"), nullable=True)
    zone_id      = Column(UUID(as_uuid=True), ForeignKey("zones.zone_id"), nullable=True)
    zone_name    = Column(String)
    object_class = Column(String)
    threat_level = Column(String)              # HIGH | MEDIUM | LOW
    summary      = Column(Text)
    status       = Column(String, default="OPEN")   # OPEN | RESOLVED
    detected_at  = Column(DateTime, default=datetime.utcnow)
    resolved_at  = Column(DateTime, nullable=True)
    metadata_json= Column(JSONB)

    detections   = relationship("Detection", foreign_keys="Detection.incident_id", lazy="dynamic")


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_camera_ts", "camera_id", "detected_at"),
        Index("ix_detections_segment",   "segment_id"),
        Index("ix_detections_incident",  "incident_id"),
    )

    detection_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id     = Column(String, nullable=False, index=True)
    camera_id    = Column(String, ForeignKey("cameras.camera_id"), nullable=False)
    segment_id   = Column(UUID(as_uuid=True), ForeignKey("segments.segment_id"), nullable=True)
    incident_id  = Column(UUID(as_uuid=True), ForeignKey("incidents.incident_id"), nullable=True)

    detected_at  = Column(DateTime, nullable=False)
    object_class = Column(String,  nullable=False)
    confidence   = Column(Float,   nullable=False)
    zone_id      = Column(String,  nullable=True)
    zone_name    = Column(String,  nullable=True)
    event_type   = Column(String,  nullable=True)   # START | UPDATE | LOITERING | END
    track_id     = Column(Integer, nullable=True)
    dwell_sec    = Column(Float,   default=0)

    bbox_x1      = Column(Float)
    bbox_y1      = Column(Float)
    bbox_x2      = Column(Float)
    bbox_y2      = Column(Float)

    frame_url    = Column(String,  nullable=True)
    thumbnail_url= Column(String,  nullable=True)
    is_threat    = Column(Boolean, nullable=True)
    threat_level = Column(String,  nullable=True)
    vlm_summary  = Column(Text,    nullable=True)
    vlm_embedding= Column(Vector(1536), nullable=True) # text-embedding-3-small (1536 dims)

    camera_rel   = relationship("Camera",   back_populates="detections")
    segment      = relationship("Segment",  back_populates="detections")
    incident     = relationship("Incident", foreign_keys=[incident_id], overlaps="detections")


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id  = Column(String)    # "global" or UUID string
    severity = Column(String)
    channels = Column(JSONB)     # ["slack", "sms"]
    config   = Column(JSONB)


class RagConfig(Base):
    __tablename__ = "rag_configs"

    key   = Column(String, primary_key=True)
    value = Column(Text)
    group = Column(String)


class UserAccount(Base):
    __tablename__ = "user_accounts"

    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="VIEWER")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
