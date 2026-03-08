"""api/routes/auth.py — Authentication endpoints."""

import os
import hmac
import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from auth.jwt_handler import create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger("api.auth")

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/token")
async def login(body: LoginRequest):
    """Issue a JWT access token for valid credentials."""
    if not (
        hmac.compare_digest(body.username, ADMIN_USER)
        and hmac.compare_digest(body.password, ADMIN_PASS)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = create_access_token({"sub": body.username, "role": "ADMIN"})
    logger.info("Token issued for user=%s", body.username)
    return {"access_token": token, "token_type": "bearer"}
