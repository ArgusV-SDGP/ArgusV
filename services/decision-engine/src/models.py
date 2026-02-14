from sqlalchemy import Column, String, Boolean, Float, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
import uuid

Base = declarative_base()

class Zone(Base):
    __tablename__ = "zones"
    
    zone_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    polygon_coords = Column(JSONB, nullable=False) # [[lat, lng], ...]
    zone_type = Column(String, default="security")
    dwell_threshold_sec = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Rule(Base):
    __tablename__ = "rules"

    rule_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zones.zone_id"))
    trigger_type = Column(String) # e.g., "loitering"
    severity = Column(String)     # "HIGH", "MEDIUM", "LOW"
    action_config = Column(JSONB) # { "siren_duration": 30 }
    is_active = Column(Boolean, default=True)

class Incident(Base):
    __tablename__ = "incidents"

    incident_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("zones.zone_id"))
    vlm_summary = Column(Text)
    confidence_score = Column(Float)
    status = Column(String, default="OPEN") # OPEN, RESOLVED
    metadata_json = Column(JSONB)
    media_url = Column(String)
    detected_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    zone_id = Column(String) # "global" or UUID string
    severity = Column(String)
    channels = Column(JSONB) # ["slack", "sms"]
    config = Column(JSONB)

class RagConfig(Base):
    __tablename__ = "rag_config"

    key = Column(String, primary_key=True)
    value = Column(Text)
    group = Column(String)
