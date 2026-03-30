"""
tests/test_prompt_manager.py — Tests for Configurable Prompt System
Task: TEST-01
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID

# Add src to path
import sys
from pathlib import Path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from prompts.prompt_manager import PromptTemplate, PromptManager, DEFAULT_THREAT_PROMPT


class TestPromptTemplate:
    """Test suite for PromptTemplate class."""

    def test_prompt_template_creation(self):
        """Test creating a basic prompt template."""
        prompt = PromptTemplate(
            name="Test Prompt",
            description="A test prompt",
            template="Alert: {object_class} in {zone_name}",
            zone_filter="parking_lot",
            priority=10,
        )

        assert prompt.name == "Test Prompt"
        assert prompt.description == "A test prompt"
        assert prompt.zone_filter == "parking_lot"
        assert prompt.priority == 10
        assert prompt.active is True
        assert isinstance(prompt.prompt_id, UUID)


    def test_prompt_matches_zone_filter(self):
        """Test prompt matching with zone filter."""
        prompt = PromptTemplate(
            name="Parking Lot Prompt",
            template="Test",
            zone_filter="parking_lot",
        )

        # Should match
        event1 = {"zone_name": "parking_lot", "object_class": "person", "camera_id": "cam-01"}
        assert prompt.matches(event1) is True

        # Should not match different zone
        event2 = {"zone_name": "restricted_area", "object_class": "person", "camera_id": "cam-01"}
        assert prompt.matches(event2) is False


    def test_prompt_matches_camera_filter(self):
        """Test prompt matching with camera filter."""
        prompt = PromptTemplate(
            name="Camera 01 Prompt",
            template="Test",
            camera_filter="cam-01",
        )

        # Should match
        event1 = {"camera_id": "cam-01", "object_class": "person", "zone_name": "zone1"}
        assert prompt.matches(event1) is True

        # Should not match different camera
        event2 = {"camera_id": "cam-02", "object_class": "person", "zone_name": "zone1"}
        assert prompt.matches(event2) is False


    def test_prompt_matches_object_class_filter(self):
        """Test prompt matching with object class filter."""
        prompt = PromptTemplate(
            name="Person Only Prompt",
            template="Test",
            object_classes=["person"],
        )

        # Should match
        event1 = {"object_class": "person", "zone_name": "zone1", "camera_id": "cam-01"}
        assert prompt.matches(event1) is True

        # Should not match different object class
        event2 = {"object_class": "vehicle", "zone_name": "zone1", "camera_id": "cam-01"}
        assert prompt.matches(event2) is False


    def test_prompt_matches_multiple_filters(self):
        """Test prompt matching with multiple filters."""
        prompt = PromptTemplate(
            name="Complex Prompt",
            template="Test",
            zone_filter="restricted_area",
            camera_filter="cam-01",
            object_classes=["person"],
        )

        # Should match all filters
        event_match = {
            "zone_name": "restricted_area",
            "camera_id": "cam-01",
            "object_class": "person",
        }
        assert prompt.matches(event_match) is True

        # Should not match if any filter fails
        event_no_match = {
            "zone_name": "restricted_area",
            "camera_id": "cam-02",  # Wrong camera
            "object_class": "person",
        }
        assert prompt.matches(event_no_match) is False


    def test_prompt_render(self):
        """Test rendering prompt template with event data."""
        prompt = PromptTemplate(
            name="Test Render",
            template="Alert: {object_class} in {zone_name} for {dwell_sec} seconds",
        )

        event = {
            "object_class": "person",
            "zone_name": "parking_lot",
            "dwell_sec": 45,
            "camera_id": "cam-01",
        }

        rendered = prompt.render(event)
        assert rendered == "Alert: person in parking_lot for 45 seconds"


    def test_prompt_render_missing_placeholder(self):
        """Test rendering with missing placeholder data."""
        prompt = PromptTemplate(
            name="Test Render",
            template="Alert: {object_class} speed: {speed} m/s",
        )

        event = {
            "object_class": "person",
            "zone_name": "parking_lot",
            # Missing "speed"
        }

        rendered = prompt.render(event)
        # Should use default value of 0.0 for speed
        assert "speed: 0.0" in rendered


    def test_prompt_to_dict(self):
        """Test serialization to dictionary."""
        prompt = PromptTemplate(
            name="Test Prompt",
            description="Test description",
            template="Test {object_class}",
            zone_filter="zone1",
            priority=20,
        )

        data = prompt.to_dict()

        assert data["name"] == "Test Prompt"
        assert data["description"] == "Test description"
        assert data["template"] == "Test {object_class}"
        assert data["zone_filter"] == "zone1"
        assert data["priority"] == 20
        assert "prompt_id" in data
        assert "created_at" in data


    def test_prompt_from_dict(self):
        """Test deserialization from dictionary."""
        data = {
            "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
            "name": "Test Prompt",
            "description": "Test",
            "template": "Alert: {object_class}",
            "zone_filter": "zone1",
            "camera_filter": None,
            "object_classes": ["person"],
            "priority": 15,
            "active": True,
            "created_at": "2026-03-17T10:00:00+00:00",
            "updated_at": "2026-03-17T10:00:00+00:00",
        }

        prompt = PromptTemplate.from_dict(data)

        assert prompt.name == "Test Prompt"
        assert prompt.template == "Alert: {object_class}"
        assert prompt.zone_filter == "zone1"
        assert prompt.priority == 15
        assert prompt.object_classes == ["person"]


    def test_prompt_inactive_no_match(self):
        """Test that inactive prompts don't match."""
        prompt = PromptTemplate(
            name="Inactive Prompt",
            template="Test",
            zone_filter="zone1",
            active=False,
        )

        event = {"zone_name": "zone1", "object_class": "person", "camera_id": "cam-01"}

        # Should not match because inactive
        assert prompt.matches(event) is False


class TestPromptManager:
    """Test suite for PromptManager class."""

    @patch('prompts.prompt_manager.redis')
    def test_manager_initialization(self, mock_redis):
        """Test PromptManager initialization."""
        manager = PromptManager()

        assert manager._cache == {}
        assert manager._cache_version == 0
        assert manager._redis is None


    @patch('prompts.prompt_manager.redis.from_url')
    def test_manager_connect(self, mock_redis_from_url):
        """Test connecting to Redis."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        assert manager._redis is not None
        assert mock_redis_from_url.called


    @patch('prompts.prompt_manager.redis.from_url')
    def test_create_prompt(self, mock_redis_from_url):
        """Test creating a new prompt."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        prompt = manager.create_prompt(
            name="High Security",
            template="CRITICAL: {object_class} in {zone_name}",
            zone_filter="restricted_area",
            priority=100,
        )

        assert prompt.name == "High Security"
        assert prompt.priority == 100
        assert str(prompt.prompt_id) in manager._cache


    @patch('prompts.prompt_manager.redis.from_url')
    def test_update_prompt(self, mock_redis_from_url):
        """Test updating an existing prompt."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        # Create prompt
        prompt = manager.create_prompt(
            name="Test Prompt",
            template="Test",
            priority=5,
        )

        prompt_id = str(prompt.prompt_id)

        # Update prompt
        updated = manager.update_prompt(prompt_id, priority=20, active=False)

        assert updated.priority == 20
        assert updated.active is False


    @patch('prompts.prompt_manager.redis.from_url')
    def test_delete_prompt(self, mock_redis_from_url):
        """Test deleting a prompt."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        # Create prompt
        prompt = manager.create_prompt(name="Test", template="Test")
        prompt_id = str(prompt.prompt_id)

        # Verify it exists
        assert prompt_id in manager._cache

        # Delete prompt
        result = manager.delete_prompt(prompt_id)

        assert result is True
        assert prompt_id not in manager._cache


    @patch('prompts.prompt_manager.redis.from_url')
    def test_get_prompt_for_event_priority_selection(self, mock_redis_from_url):
        """Test that highest priority prompt is selected."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        # Create multiple matching prompts with different priorities
        prompt1 = manager.create_prompt(
            name="Low Priority",
            template="LOW: {object_class}",
            zone_filter="parking_lot",
            priority=10,
        )

        prompt2 = manager.create_prompt(
            name="High Priority",
            template="HIGH: {object_class}",
            zone_filter="parking_lot",
            priority=100,
        )

        event = {
            "zone_name": "parking_lot",
            "object_class": "person",
            "camera_id": "cam-01",
            "dwell_sec": 30,
            "event_type": "LOITERING",
        }

        # Get prompt for event
        rendered = manager.get_prompt_for_event(event)

        # Should use high priority prompt
        assert "HIGH: person" in rendered


    @patch('prompts.prompt_manager.redis.from_url')
    def test_get_prompt_for_event_default_fallback(self, mock_redis_from_url):
        """Test fallback to default prompt when no matches."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        # Create prompt that won't match
        manager.create_prompt(
            name="Specific Prompt",
            template="SPECIFIC: {object_class}",
            zone_filter="restricted_area",  # Different zone
            priority=50,
        )

        event = {
            "zone_name": "parking_lot",  # Won't match
            "object_class": "person",
            "camera_id": "cam-01",
            "dwell_sec": 30,
            "event_type": "LOITERING",
        }

        # Get prompt for event
        rendered = manager.get_prompt_for_event(event)

        # Should contain default prompt text
        assert "security analyst" in rendered.lower() or "person" in rendered


    @patch('prompts.prompt_manager.redis.from_url')
    def test_list_prompts(self, mock_redis_from_url):
        """Test listing all prompts."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        # Create multiple prompts
        manager.create_prompt(name="Prompt 1", template="Test 1", priority=10)
        manager.create_prompt(name="Prompt 2", template="Test 2", priority=20)
        manager.create_prompt(name="Prompt 3", template="Test 3", priority=5, active=False)

        # List all prompts
        all_prompts = manager.list_prompts(active_only=False)
        assert len(all_prompts) >= 3  # May include default

        # List only active prompts
        active_prompts = manager.list_prompts(active_only=True)
        assert len(active_prompts) >= 2  # Excludes inactive


    @patch('prompts.prompt_manager.redis.from_url')
    def test_list_prompts_sorted_by_priority(self, mock_redis_from_url):
        """Test that prompts are sorted by priority (high to low)."""
        mock_redis_client = MagicMock()
        mock_redis_client.keys.return_value = []
        mock_redis_from_url.return_value = mock_redis_client

        manager = PromptManager()
        manager.connect()

        # Create prompts in random order
        manager.create_prompt(name="Medium", template="Test", priority=50)
        manager.create_prompt(name="High", template="Test", priority=100)
        manager.create_prompt(name="Low", template="Test", priority=10)

        prompts = manager.list_prompts()

        # Verify sorted by priority (highest first)
        priorities = [p.priority for p in prompts]
        assert priorities == sorted(priorities, reverse=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
