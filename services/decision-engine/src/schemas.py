from pydantic import BaseModel, ConfigDict
from enum import Enum
from typing import List, Optional, Dict
from datetime import datetime
from uuid import UUID

# Shared Enums
class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"

# Zone Configuration
class ZoneCreate(BaseModel):
    name: str
    polygon_coords: List[List[float]] # [[lat, lng], ...]
    zone_type: str = "security"
    dwell_threshold_sec: int = 0
    is_active: bool = True

class ZoneUpdate(BaseModel):
    name: Optional[str] = None
    polygon_coords: Optional[List[List[float]]] = None
    zone_type: Optional[str] = None
    dwell_threshold_sec: Optional[int] = None
    is_active: Optional[bool] = None

class ZoneResponse(ZoneCreate):
    zone_id: UUID
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Notification Rules
class NotificationRuleCreate(BaseModel):
    zone_id: Optional[str] = "global"
    severity: Severity
    channels: List[str]
    config: Dict

class NotificationRuleResponse(NotificationRuleCreate):
    id: UUID
    model_config = ConfigDict(from_attributes=True)

# RAG Configuration
class RagConfigUpdate(BaseModel):
    key: str
    value: str
    group: str
