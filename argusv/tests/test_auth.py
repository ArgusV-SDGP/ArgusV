"""tests/test_auth.py — AUTH-06 unit tests for JWT + API key auth."""

import time
import pytest
import jwt as pyjwt
from unittest.mock import patch
from fastapi import HTTPException

from auth.jwt_handler import (
    create_access_token,
    verify_token,
    get_current_user,
    SECRET_KEY,
    ALGORITHM,
)


# ── create_access_token ──────────────────────────────────────────────────────

def test_create_token_returns_string():
    token = create_access_token({"sub": "admin"})
    assert isinstance(token, str)


def test_create_token_contains_claims():
    token = create_access_token({"sub": "admin", "role": "ADMIN"})
    payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "admin"
    assert payload["role"] == "ADMIN"
    assert "exp" in payload
    assert "iat" in payload


def test_create_token_custom_expiry():
    token = create_access_token({"sub": "u"}, expires_minutes=5)
    payload = pyjwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    # exp should be ~5 min from iat
    assert 4 * 60 <= (payload["exp"] - payload["iat"]) <= 6 * 60


def test_create_token_does_not_mutate_input():
    data = {"sub": "admin"}
    create_access_token(data)
    assert "exp" not in data
    assert "iat" not in data


# ── verify_token ─────────────────────────────────────────────────────────────

def test_verify_valid_token():
    token = create_access_token({"sub": "admin"})
    payload = verify_token(token)
    assert payload["sub"] == "admin"


def test_verify_expired_token():
    token = create_access_token({"sub": "admin"}, expires_minutes=-1)
    with pytest.raises(HTTPException) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_verify_invalid_token():
    with pytest.raises(HTTPException) as exc_info:
        verify_token("not-a-real-token")
    assert exc_info.value.status_code == 401
    assert "invalid" in exc_info.value.detail.lower()


def test_verify_wrong_secret():
    token = pyjwt.encode({"sub": "x", "exp": time.time() + 300}, "wrong-secret", algorithm=ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401


# ── get_current_user ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_current_user_no_credentials():
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_valid_jwt():
    token = create_access_token({"sub": "admin", "role": "ADMIN"})

    class FakeCreds:
        scheme = "Bearer"
        credentials = token

    user = await get_current_user(credentials=FakeCreds())
    assert user["user"] == "admin"
    assert user["role"] == "ADMIN"


@pytest.mark.asyncio
async def test_get_current_user_invalid_jwt():
    class FakeCreds:
        scheme = "Bearer"
        credentials = "garbage"

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=FakeCreds())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_api_key():
    test_key = "my-secret-api-key-123"
    with patch("auth.jwt_handler.API_KEYS", {test_key}):
        class FakeCreds:
            scheme = "Bearer"
            credentials = test_key

        user = await get_current_user(credentials=FakeCreds())
        assert user["user"] == "api-key"
        assert user["role"] == "ADMIN"


@pytest.mark.asyncio
async def test_get_current_user_bad_api_key():
    with patch("auth.jwt_handler.API_KEYS", {"real-key"}):
        class FakeCreds:
            scheme = "Bearer"
            credentials = "wrong-key"

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(credentials=FakeCreds())
        assert exc_info.value.status_code == 401
