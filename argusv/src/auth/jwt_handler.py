"""
auth/jwt_handler.py — JWT authentication
-----------------------------------------
Tasks: AUTH-01, AUTH-06, AUTH-07

BRAYAN NOTE: Auth is fully stubbed — nothing works yet.
  - create_access_token() → raises NotImplementedError (AUTH-01 TODO)
  - verify_token()        → raises NotImplementedError (AUTH-01 TODO)
  - get_current_user()    → returns {"user": "anonymous", "role": "ADMIN"} for ALL requests
  Secret: JWT_SECRET env var (default "change-me-in-production"), HS256, 60min expiry.
  My job (AUTH-03): Wire frontend fetch to POST /api/auth/token, attach Bearer header,
  handle 401 → redirect to login. Backend implementation is DEV-3's responsibility.
"""
# TODO AUTH-01: POST /auth/token → issue JWT
# TODO AUTH-06: API key auth (Bearer token)
# TODO AUTH-07: Refresh tokens

import os
from datetime import datetime, timedelta
from typing import Optional
import jwt
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SECRET_KEY   = os.getenv("JWT_SECRET", "change-me-in-production")
ALGORITHM    = "HS256"
TOKEN_EXPIRE = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

security = HTTPBearer(auto_error=False)


def create_access_token(data: dict, expires_minutes: int = TOKEN_EXPIRE) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload["iat"] = datetime.utcnow()
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """FastAPI dependency — use on protected routes."""
    # TODO AUTH-02: verify token, return user
    # For now: allow all (no auth)
    return {"user": "anonymous", "role": "ADMIN"}
