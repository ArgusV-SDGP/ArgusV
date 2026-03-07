"""
auth/jwt_handler.py — JWT authentication
-----------------------------------------
Tasks: AUTH-01, AUTH-06, AUTH-07
"""
# TODO AUTH-01: POST /auth/token → issue JWT
# TODO AUTH-06: API key auth (Bearer token)
# TODO AUTH-07: Refresh tokens

import os
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SECRET_KEY   = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM    = "HS256"
TOKEN_EXPIRE = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

security = HTTPBearer(auto_error=False)


def create_access_token(data: dict, expires_minutes: int = TOKEN_EXPIRE) -> str:
    # TODO AUTH-01: implement with python-jose or PyJWT
    raise NotImplementedError("TODO AUTH-01: implement JWT creation")


def verify_token(token: str) -> dict:
    # TODO AUTH-01: decode + verify JWT
    raise NotImplementedError("TODO AUTH-01: implement JWT verification")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """FastAPI dependency — use on protected routes."""
    # TODO AUTH-02: verify token, return user
    # For now: allow all (no auth)
    return {"user": "anonymous", "role": "ADMIN"}
