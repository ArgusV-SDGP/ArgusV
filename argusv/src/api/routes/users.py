from typing import Any, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

import config as cfg
from auth.jwt_handler import ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, require_roles
from auth.passwords import hash_password
from db.connection import get_db
from db.models import UserAccount

router = APIRouter(prefix="/api/users", tags=["users"])

ALLOWED_ROLES = {ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default=ROLE_VIEWER)
    is_active: bool = True

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = value.strip()
        if not username:
            raise ValueError("username is required")
        if " " in username:
            raise ValueError("username cannot contain spaces")
        return username

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        role = (value or ROLE_VIEWER).strip().upper()
        if role not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of {sorted(ALLOWED_ROLES)}")
        return role


class UserPatch(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=256)
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        role = value.strip().upper()
        if role not in ALLOWED_ROLES:
            raise ValueError(f"role must be one of {sorted(ALLOWED_ROLES)}")
        return role


def _serialize_user(user: UserAccount) -> dict[str, Any]:
    return {
        "user_id": str(user.user_id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("")
def list_users(
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    rows = db.query(UserAccount).order_by(UserAccount.created_at.desc(), UserAccount.username.asc()).all()
    return [_serialize_user(row) for row in rows]


@router.post("", status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    exists_in_db = db.query(UserAccount).filter(UserAccount.username == payload.username).first()
    if exists_in_db or payload.username in cfg.AUTH_USERS:
        raise HTTPException(409, "username already exists")

    user = UserAccount(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _serialize_user(user)


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    payload: UserPatch,
    db: Session = Depends(get_db),
    admin_user: dict = Depends(require_roles(ROLE_ADMIN)),
):
    try:
        parsed_user_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(400, "Invalid user_id")

    user = db.query(UserAccount).filter(UserAccount.user_id == parsed_user_id).first()
    if not user:
        raise HTTPException(404, "User not found")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _serialize_user(user)

    if user.username == admin_user.get("username"):
        if changes.get("role") and changes["role"] != ROLE_ADMIN:
            raise HTTPException(400, "Cannot change your own admin role")
        if changes.get("is_active") is False:
            raise HTTPException(400, "Cannot deactivate your own account")

    if "password" in changes:
        user.password_hash = hash_password(changes.pop("password"))
    if "role" in changes:
        user.role = changes["role"]
    if "is_active" in changes:
        user.is_active = changes["is_active"]

    db.commit()
    db.refresh(user)
    return _serialize_user(user)
