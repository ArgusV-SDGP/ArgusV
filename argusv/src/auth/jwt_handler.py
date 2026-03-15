"""
auth/jwt_handler.py — JWT authentication
-----------------------------------------
Tasks: AUTH-01, AUTH-06, AUTH-07
"""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config as cfg

ALGORITHM = "HS256"
ROLE_ADMIN = "ADMIN"
ROLE_OPERATOR = "OPERATOR"
ROLE_VIEWER = "VIEWER"
ROLE_SERVICE = "SERVICE"

security = HTTPBearer(auto_error=False)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + pad).encode("utf-8"))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hmac_signature(content: str) -> str:
    digest = hmac.new(
        cfg.JWT_SECRET.encode("utf-8"),
        content.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


def create_access_token(data: dict[str, Any], expires_minutes: int = cfg.JWT_EXPIRE_MINUTES) -> str:
    return _create_token(data, expires_minutes=expires_minutes, token_type="access")


def create_refresh_token(data: dict[str, Any], expires_minutes: int = cfg.JWT_REFRESH_EXPIRE_MINUTES) -> str:
    return _create_token(data, expires_minutes=expires_minutes, token_type="refresh")


def _create_token(data: dict[str, Any], expires_minutes: int, token_type: str) -> str:
    header = {"alg": ALGORITHM, "typ": "JWT"}
    now = _now_utc()
    payload = {
        **data,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "token_type": token_type,
    }
    h = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    p = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _hmac_signature(f"{h}.{p}")
    return f"{h}.{p}.{signature}"


def verify_token(token: str, expected_type: Optional[str] = None) -> dict[str, Any]:
    try:
        h, p, s = token.split(".")
    except ValueError:
        raise HTTPException(401, "Invalid token format")

    signed_content = f"{h}.{p}"
    expected_sig = _hmac_signature(signed_content)
    if not hmac.compare_digest(expected_sig, s):
        raise HTTPException(401, "Invalid token signature")

    try:
        payload = json.loads(_b64url_decode(p).decode("utf-8"))
    except Exception:
        raise HTTPException(401, "Invalid token payload")

    exp = payload.get("exp")
    if not isinstance(exp, int) or _now_utc().timestamp() > exp:
        raise HTTPException(401, "Token expired")

    token_type = payload.get("token_type")
    if expected_type and token_type != expected_type:
        raise HTTPException(401, f"Invalid token type: expected {expected_type}")

    return payload


def _auth_error() -> HTTPException:
    return HTTPException(401, "Authentication required")


def _normalize_role(role: Optional[str]) -> str:
    return (role or ROLE_VIEWER).upper()


def _resolve_api_key_user(api_key: str) -> Optional[dict[str, Any]]:
    row = cfg.API_KEYS.get(api_key)
    if not isinstance(row, dict):
        return None
    return {
        "auth_type": "api_key",
        "subject": row.get("subject", "api-key-user"),
        "username": row.get("subject", "api-key-user"),
        "role": _normalize_role(row.get("role", ROLE_SERVICE)),
        "scopes": row.get("scopes", []),
    }


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    proxy_user: Optional[str] = Header(default=None, alias=cfg.PROXY_AUTH_USER_HEADER),
    proxy_role: Optional[str] = Header(default=None, alias=cfg.PROXY_AUTH_ROLE_HEADER),
):
    if cfg.DEV_AUTH_BYPASS:
        return {
            "auth_type": "bypass",
            "subject": "dev-admin",
            "username": "dev-admin",
            "role": ROLE_ADMIN,
            "scopes": [],
        }

    if credentials and credentials.scheme.lower() == "bearer":
        payload = verify_token(credentials.credentials, expected_type="access")
        return {
            "auth_type": "jwt",
            "subject": payload.get("sub", payload.get("username")),
            "username": payload.get("username", payload.get("sub", "unknown")),
            "role": _normalize_role(payload.get("role")),
            "scopes": payload.get("scopes", []),
        }

    if x_api_key:
        user = _resolve_api_key_user(x_api_key)
        if user:
            return user
        raise HTTPException(401, "Invalid API key")

    if cfg.PROXY_AUTH_ENABLED and proxy_user:
        return {
            "auth_type": "proxy",
            "subject": proxy_user,
            "username": proxy_user,
            "role": _normalize_role(proxy_role),
            "scopes": [],
        }

    raise _auth_error()


def require_roles(*allowed_roles: str):
    allowed = {r.upper() for r in allowed_roles}

    async def _guard(user: dict[str, Any] = Depends(get_current_user)):
        role = _normalize_role(user.get("role"))
        if role not in allowed:
            raise HTTPException(403, f"Insufficient role: required one of {sorted(allowed)}")
        return user

    return _guard
