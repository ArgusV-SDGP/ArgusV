"""
prompts/prompt_manager.py — Configurable Prompt System
-------------------------------------------------------
Custom threat detection prompts for specific scenarios.

Allows administrators to configure:
- Custom VLM prompts for specific zones/cameras
- Threat detection rules based on prompts
- Context-specific threat categorization
- Custom object class definitions
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import redis

import config as cfg

logger = logging.getLogger("prompts.manager")


DEFAULT_THREAT_PROMPT = (
    "You are a security analyst reviewing camera footage. "
    "A {object_class} was detected in '{zone_name}' (dwell: {dwell_sec}s, type: {event_type}). "
    "Analyse this scene. Respond with ONLY valid JSON: "
    '{"threat_level":"HIGH|MEDIUM|LOW","is_threat":true|false,'
    '"summary":"<1 sentence>","recommended_action":"ALERT|MONITOR|IGNORE"}'
)


class PromptTemplate:
    """
    Configurable prompt template for threat detection.

    Attributes:
        prompt_id: Unique identifier
        name: Human-readable name
        description: What this prompt is for
        template: The prompt template with {placeholders}
        zone_filter: Apply only to specific zone (None = all zones)
        camera_filter: Apply only to specific camera (None = all cameras)
        object_classes: Apply only to these object classes (empty = all)
        priority: Higher priority templates override lower ones
        active: Whether this template is currently active
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """

    def __init__(
        self,
        prompt_id: Optional[UUID] = None,
        name: str = "Default",
        description: str = "",
        template: str = DEFAULT_THREAT_PROMPT,
        zone_filter: Optional[str] = None,
        camera_filter: Optional[str] = None,
        object_classes: Optional[list[str]] = None,
        priority: int = 0,
        active: bool = True,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ):
        self.prompt_id = prompt_id or uuid4()
        self.name = name
        self.description = description
        self.template = template
        self.zone_filter = zone_filter
        self.camera_filter = camera_filter
        self.object_classes = object_classes or []
        self.priority = priority
        self.active = active
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)

    def matches(self, event: dict) -> bool:
        """Check if this template applies to the given event"""
        if not self.active:
            return False

        # Zone filter
        if self.zone_filter and event.get("zone_name") != self.zone_filter:
            return False

        # Camera filter
        if self.camera_filter and event.get("camera_id") != self.camera_filter:
            return False

        # Object class filter
        if self.object_classes and event.get("object_class") not in self.object_classes:
            return False

        return True

    def render(self, event: dict) -> str:
        """Render the prompt template with event data"""
        context = {
            "object_class": event.get("object_class", "unknown"),
            "zone_name": event.get("zone_name", "unknown"),
            "camera_id": event.get("camera_id", "unknown"),
            "dwell_sec": event.get("dwell_sec", 0),
            "event_type": event.get("event_type", "DETECTED"),
            "confidence": event.get("confidence", 0.0),
            "speed": event.get("speed", 0.0),
        }

        try:
            return self.template.format(**context)
        except KeyError as e:
            logger.warning(f"[Prompts] Missing placeholder in template: {e}")
            return self.template

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "prompt_id": str(self.prompt_id),
            "name": self.name,
            "description": self.description,
            "template": self.template,
            "zone_filter": self.zone_filter,
            "camera_filter": self.camera_filter,
            "object_classes": self.object_classes,
            "priority": self.priority,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptTemplate":
        """Deserialize from dictionary"""
        return cls(
            prompt_id=UUID(data["prompt_id"]) if data.get("prompt_id") else None,
            name=data.get("name", "Default"),
            description=data.get("description", ""),
            template=data.get("template", DEFAULT_THREAT_PROMPT),
            zone_filter=data.get("zone_filter"),
            camera_filter=data.get("camera_filter"),
            object_classes=data.get("object_classes", []),
            priority=data.get("priority", 0),
            active=data.get("active", True),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
        )


class PromptManager:
    """
    Manages configurable threat detection prompts.

    Stores prompts in Redis for hot-reload capability.
    Workers query the manager to get the appropriate prompt for each detection.
    """

    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._cache: dict[str, PromptTemplate] = {}
        self._cache_version = 0

    def connect(self):
        """Connect to Redis"""
        try:
            self._redis = redis.from_url(cfg.REDIS_URL, decode_responses=True)
            self._load_prompts()
            logger.info("[Prompts] Connected to Redis and loaded prompts")
        except Exception as e:
            logger.error(f"[Prompts] Failed to connect to Redis: {e}")
            # Use default prompt if Redis unavailable
            self._cache["default"] = PromptTemplate()

    def _load_prompts(self):
        """Load all prompts from Redis into cache"""
        if not self._redis:
            return

        try:
            # Get all prompt keys
            keys = self._redis.keys("prompt:*")

            self._cache.clear()

            for key in keys:
                prompt_data = self._redis.get(key)
                if prompt_data:
                    prompt = PromptTemplate.from_dict(json.loads(prompt_data))
                    self._cache[str(prompt.prompt_id)] = prompt

            # Ensure we have at least one default prompt
            if not self._cache:
                default = PromptTemplate()
                self._cache[str(default.prompt_id)] = default
                self._save_prompt(default)

            self._cache_version += 1
            logger.info(f"[Prompts] Loaded {len(self._cache)} prompt templates")

        except Exception as e:
            logger.error(f"[Prompts] Failed to load prompts: {e}")

    def _save_prompt(self, prompt: PromptTemplate):
        """Save prompt to Redis"""
        if not self._redis:
            return

        try:
            key = f"prompt:{prompt.prompt_id}"
            self._redis.set(key, json.dumps(prompt.to_dict()))
            logger.debug(f"[Prompts] Saved prompt: {prompt.name}")
        except Exception as e:
            logger.error(f"[Prompts] Failed to save prompt: {e}")

    def get_prompt_for_event(self, event: dict) -> str:
        """
        Get the most appropriate prompt template for an event.

        Selects based on:
        1. Zone/camera/object class filters
        2. Priority (higher wins)
        3. Falls back to default

        Args:
            event: Detection event dict

        Returns:
            Rendered prompt string
        """
        # Find all matching templates
        matches = [
            prompt for prompt in self._cache.values()
            if prompt.matches(event)
        ]

        if not matches:
            # No matches - use default
            default = next((p for p in self._cache.values() if p.name == "Default"), None)
            if default:
                return default.render(event)
            else:
                return DEFAULT_THREAT_PROMPT.format(
                    object_class=event.get("object_class", "unknown"),
                    zone_name=event.get("zone_name", "unknown"),
                    dwell_sec=event.get("dwell_sec", 0),
                    event_type=event.get("event_type", "DETECTED"),
                )

        # Sort by priority (highest first)
        matches.sort(key=lambda p: p.priority, reverse=True)

        # Use highest priority match
        return matches[0].render(event)

    def create_prompt(
        self,
        name: str,
        template: str,
        description: str = "",
        zone_filter: Optional[str] = None,
        camera_filter: Optional[str] = None,
        object_classes: Optional[list[str]] = None,
        priority: int = 0,
    ) -> PromptTemplate:
        """
        Create and save a new prompt template.

        Args:
            name: Template name
            template: Prompt template with {placeholders}
            description: What this is for
            zone_filter: Specific zone (None = all)
            camera_filter: Specific camera (None = all)
            object_classes: Specific object classes (empty = all)
            priority: Priority level (higher = preferred)

        Returns:
            Created PromptTemplate
        """
        prompt = PromptTemplate(
            name=name,
            description=description,
            template=template,
            zone_filter=zone_filter,
            camera_filter=camera_filter,
            object_classes=object_classes or [],
            priority=priority,
        )

        self._cache[str(prompt.prompt_id)] = prompt
        self._save_prompt(prompt)

        # Publish update event
        if self._redis:
            self._redis.publish("config-updates", json.dumps({
                "type": "PROMPT_CREATED",
                "prompt_id": str(prompt.prompt_id),
                "name": name,
            }))

        logger.info(f"[Prompts] Created prompt: {name}")
        return prompt

    def update_prompt(self, prompt_id: str, **updates) -> Optional[PromptTemplate]:
        """Update an existing prompt template"""
        prompt = self._cache.get(prompt_id)
        if not prompt:
            logger.warning(f"[Prompts] Prompt not found: {prompt_id}")
            return None

        # Update fields
        for key, value in updates.items():
            if hasattr(prompt, key):
                setattr(prompt, key, value)

        prompt.updated_at = datetime.now(timezone.utc)

        self._save_prompt(prompt)

        # Publish update
        if self._redis:
            self._redis.publish("config-updates", json.dumps({
                "type": "PROMPT_UPDATED",
                "prompt_id": prompt_id,
            }))

        logger.info(f"[Prompts] Updated prompt: {prompt.name}")
        return prompt

    def delete_prompt(self, prompt_id: str) -> bool:
        """Delete a prompt template"""
        if prompt_id not in self._cache:
            return False

        prompt = self._cache.pop(prompt_id)

        if self._redis:
            self._redis.delete(f"prompt:{prompt_id}")
            self._redis.publish("config-updates", json.dumps({
                "type": "PROMPT_DELETED",
                "prompt_id": prompt_id,
            }))

        logger.info(f"[Prompts] Deleted prompt: {prompt.name}")
        return True

    def list_prompts(self, active_only: bool = False) -> list[PromptTemplate]:
        """Get all prompts"""
        prompts = list(self._cache.values())

        if active_only:
            prompts = [p for p in prompts if p.active]

        # Sort by priority
        prompts.sort(key=lambda p: p.priority, reverse=True)

        return prompts

    def get_prompt(self, prompt_id: str) -> Optional[PromptTemplate]:
        """Get a specific prompt by ID"""
        return self._cache.get(prompt_id)


# Global singleton
_prompt_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """Get or create global prompt manager"""
    global _prompt_manager

    if _prompt_manager is None:
        _prompt_manager = PromptManager()
        _prompt_manager.connect()

    return _prompt_manager
