"""
api/routes/prompts.py — Prompt Configuration Management
--------------------------------------------------------
Admin API for managing custom threat detection prompts.

Endpoints:
- GET /api/prompts - List all prompts
- GET /api/prompts/{prompt_id} - Get specific prompt
- POST /api/prompts - Create new prompt
- PUT /api/prompts/{prompt_id} - Update prompt
- DELETE /api/prompts/{prompt_id} - Delete prompt
- POST /api/prompts/test - Test prompt rendering
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, require_roles
from prompts.prompt_manager import get_prompt_manager

router = APIRouter(prefix="/api/prompts", tags=["prompts"])
logger = logging.getLogger("api.prompts")


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    template: str = Field(min_length=10, max_length=2000)
    zone_filter: Optional[str] = None
    camera_filter: Optional[str] = None
    object_classes: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)


class PromptUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    template: Optional[str] = Field(default=None, min_length=10, max_length=2000)
    zone_filter: Optional[str] = None
    camera_filter: Optional[str] = None
    object_classes: Optional[list[str]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=100)
    active: Optional[bool] = None


class PromptTestRequest(BaseModel):
    template: str
    event_data: dict


@router.get("")
def list_prompts(
    active_only: bool = False,
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """List all prompt templates"""
    manager = get_prompt_manager()
    prompts = manager.list_prompts(active_only=active_only)

    return [prompt.to_dict() for prompt in prompts]


@router.get("/{prompt_id}")
def get_prompt(
    prompt_id: str,
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """Get a specific prompt template"""
    manager = get_prompt_manager()
    prompt = manager.get_prompt(prompt_id)

    if not prompt:
        raise HTTPException(404, "Prompt not found")

    return prompt.to_dict()


@router.post("", status_code=201)
def create_prompt(
    payload: PromptCreate,
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    """
    Create a new prompt template.

    Only ADMIN users can create prompts.

    Available placeholders in template:
    - {object_class} - Detected object class (person, vehicle, etc.)
    - {zone_name} - Zone name where detection occurred
    - {camera_id} - Camera ID
    - {dwell_sec} - How long object has been in zone
    - {event_type} - Event type (START, UPDATE, LOITERING, END)
    - {confidence} - Detection confidence score
    - {speed} - Object speed (if tracked)

    Example template:
    "Alert: {object_class} detected in {zone_name} for {dwell_sec} seconds.
    Assess threat level and respond with JSON."
    """
    manager = get_prompt_manager()

    prompt = manager.create_prompt(
        name=payload.name,
        template=payload.template,
        description=payload.description,
        zone_filter=payload.zone_filter,
        camera_filter=payload.camera_filter,
        object_classes=payload.object_classes,
        priority=payload.priority,
    )

    return prompt.to_dict()


@router.put("/{prompt_id}")
def update_prompt(
    prompt_id: str,
    payload: PromptUpdate,
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    """Update an existing prompt template"""
    manager = get_prompt_manager()

    # Build update dict (exclude None values)
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}

    if not updates:
        raise HTTPException(400, "No fields to update")

    prompt = manager.update_prompt(prompt_id, **updates)

    if not prompt:
        raise HTTPException(404, "Prompt not found")

    return prompt.to_dict()


@router.delete("/{prompt_id}", status_code=204)
def delete_prompt(
    prompt_id: str,
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    """Delete a prompt template"""
    manager = get_prompt_manager()

    success = manager.delete_prompt(prompt_id)

    if not success:
        raise HTTPException(404, "Prompt not found")


@router.post("/test")
def test_prompt(
    payload: PromptTestRequest,
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """
    Test a prompt template by rendering it with sample event data.

    Useful for validating prompt templates before saving.

    Example request:
    {
      "template": "Alert: {object_class} in {zone_name} for {dwell_sec}s",
      "event_data": {
        "object_class": "person",
        "zone_name": "restricted_area",
        "dwell_sec": 45
      }
    }
    """
    try:
        rendered = payload.template.format(**payload.event_data)
        return {
            "rendered": rendered,
            "status": "success",
        }
    except KeyError as e:
        return {
            "rendered": None,
            "status": "error",
            "error": f"Missing placeholder: {e}",
        }
    except Exception as e:
        return {
            "rendered": None,
            "status": "error",
            "error": str(e),
        }


@router.get("/placeholders/available")
def get_available_placeholders(
    _user: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
):
    """
    Get list of available template placeholders with descriptions.
    """
    return {
        "placeholders": [
            {
                "name": "{object_class}",
                "description": "Detected object class (person, vehicle, animal, etc.)",
                "example": "person",
            },
            {
                "name": "{zone_name}",
                "description": "Name of the zone where detection occurred",
                "example": "restricted_area",
            },
            {
                "name": "{camera_id}",
                "description": "Camera identifier",
                "example": "cam-01",
            },
            {
                "name": "{dwell_sec}",
                "description": "How long object has been in the zone (seconds)",
                "example": "45",
            },
            {
                "name": "{event_type}",
                "description": "Type of detection event",
                "example": "LOITERING",
                "possible_values": ["START", "UPDATE", "LOITERING", "END"],
            },
            {
                "name": "{confidence}",
                "description": "Detection confidence score (0.0 to 1.0)",
                "example": "0.87",
            },
            {
                "name": "{speed}",
                "description": "Object speed (if tracking enabled)",
                "example": "2.5",
            },
        ]
    }
