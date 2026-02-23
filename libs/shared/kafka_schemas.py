from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, List, Dict, Any


class RawDetection(BaseModel):
    event_id: str  # UUID
    camera_id: str
    zone_id: str
    detected_objects: List[str]  # e.g., ["person", "backpack"]
    confidence_scores: List[float]
    frame_timestamp: datetime
    produced_at: datetime = Field(default_factory=datetime.utcnow)

class VlmRequest(BaseModel):
    event_id: str
    camera_id: str
    zone_id: str
    frame_urls: List[str]  # MinIO presigned URLs
    detection_context: RawDetection

class VlmResult(BaseModel):
    event_id: str
    threat_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    summary: str
    confidence: float
    recommended_action: str
    processed_at: datetime = Field(default_factory=datetime.utcnow)

class Action(BaseModel):
    action_id: str
    event_id: str
    action_type: Literal["alert", "actuate"]
    target: str  # e.g., "#security-slack" or "door-lock-01"
    payload: Dict[str, Any]

class ConfigUpdate(BaseModel):
    update_type: Literal["zone", "rule", "camera"]
    operation: Literal["create", "update", "delete"]
    entity_id: str
    data: Dict[str, Any]