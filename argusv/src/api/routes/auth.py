"""api/routes/auth.py — JWT issuance, refresh, and identity checks."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import config as cfg
from auth.passwords import hash_password, verify_password
from auth.jwt_handler import (
    ROLE_ADMIN,
    ROLE_VIEWER,
    create_access_token,
    create_refresh_token,
    get_current_user,
    verify_token,
)
from db.connection import get_db
from db.models import UserAccount

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


def _is_password_match(raw_password: str, stored_password: str) -> bool:
    if stored_password.startswith("pbkdf2_sha256$"):
        return verify_password(raw_password, stored_password)
    return raw_password == stored_password


def _issue_tokens(username: str, role: str, scopes: list[str] | None = None) -> dict[str, Any]:
    claims = {
        "sub": username,
        "username": username,
        "role": role.upper(),
        "scopes": scopes or [],
    }
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in_sec": cfg.JWT_EXPIRE_MINUTES * 60,
    }


@router.post("/token")
def issue_token(payload: TokenRequest, db: Session = Depends(get_db)):
    db_user = (
        db.query(UserAccount)
        .filter(UserAccount.username == payload.username, UserAccount.is_active == True)
        .first()
    )
    if db_user:
        if not _is_password_match(payload.password, db_user.password_hash):
            raise HTTPException(401, "Invalid username or password")
        return _issue_tokens(
            username=db_user.username,
            role=db_user.role or ROLE_VIEWER,
            scopes=[],
        )

    row = cfg.AUTH_USERS.get(payload.username)
    if not isinstance(row, dict):
        raise HTTPException(401, "Invalid username or password")
    password = row.get("password")
    if not isinstance(password, str) or not _is_password_match(payload.password, password):
        raise HTTPException(401, "Invalid username or password")

    return _issue_tokens(
        username=payload.username,
        role=row.get("role", ROLE_ADMIN),
        scopes=row.get("scopes", []),
    )


@router.post("/refresh")
def refresh_token(payload: RefreshRequest):
    claims = verify_token(payload.refresh_token, expected_type="refresh")
    username = claims.get("username", claims.get("sub"))
    if not username:
        raise HTTPException(401, "Invalid refresh token")
    return _issue_tokens(
        username=username,
        role=claims.get("role", ROLE_ADMIN),
        scopes=claims.get("scopes", []),
    )


@router.get("/me")
def who_am_i(user: dict[str, Any] = Depends(get_current_user)):
    return {
        "subject": user.get("subject"),
        "username": user.get("username"),
        "role": user.get("role"),
        "auth_type": user.get("auth_type"),
        "at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/register", status_code=201)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)):
    username = payload.username.strip()
    if not username:
        raise HTTPException(400, "username is required")
    if " " in username:
        raise HTTPException(400, "username cannot contain spaces")

    exists_in_db = db.query(UserAccount).filter(UserAccount.username == username).first()
    if exists_in_db or username in cfg.AUTH_USERS:
        raise HTTPException(409, "username already exists")

    user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        role=ROLE_VIEWER,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "user_id": str(user.user_id),
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }
