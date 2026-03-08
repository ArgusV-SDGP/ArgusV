"""tests/test_auth.py — auth token and API key checks."""

from datetime import datetime, timezone
import uuid

import pytest
from fastapi import HTTPException

from auth import jwt_handler
from auth.passwords import hash_password, verify_password
from api.routes import auth as auth_routes


def test_access_token_create_and_verify():
    token = jwt_handler.create_access_token({"sub": "admin", "username": "admin", "role": "ADMIN"})
    payload = jwt_handler.verify_token(token, expected_type="access")
    assert payload["sub"] == "admin"
    assert payload["role"] == "ADMIN"


def test_refresh_token_rejected_as_access():
    token = jwt_handler.create_refresh_token({"sub": "admin", "username": "admin", "role": "ADMIN"})
    with pytest.raises(HTTPException):
        jwt_handler.verify_token(token, expected_type="access")


def test_issue_token_with_valid_credentials(monkeypatch):
    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

    monkeypatch.setattr(auth_routes.cfg, "AUTH_USERS", {"alice": {"password": "pw", "role": "OPERATOR"}})
    result = auth_routes.issue_token(auth_routes.TokenRequest(username="alice", password="pw"), db=FakeDb())
    assert "access_token" in result
    assert "refresh_token" in result
    access = jwt_handler.verify_token(result["access_token"], expected_type="access")
    assert access["username"] == "alice"


@pytest.mark.asyncio
async def test_get_current_user_api_key(monkeypatch):
    monkeypatch.setattr(
        jwt_handler.cfg,
        "API_KEYS",
        {"unit-test-key": {"subject": "service-client", "role": "SERVICE"}},
    )
    user = await jwt_handler.get_current_user(credentials=None, x_api_key="unit-test-key")
    assert user["auth_type"] == "api_key"
    assert user["role"] == "SERVICE"


def test_issue_token_with_db_user(monkeypatch):
    class FakeDbUser:
        username = "dbuser"
        password_hash = hash_password("DbPass1234")
        role = "OPERATOR"

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return FakeDbUser()

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

    monkeypatch.setattr(auth_routes.cfg, "AUTH_USERS", {})
    result = auth_routes.issue_token(
        auth_routes.TokenRequest(username="dbuser", password="DbPass1234"),
        db=FakeDb(),
    )
    access = jwt_handler.verify_token(result["access_token"], expected_type="access")
    assert access["username"] == "dbuser"
    assert access["role"] == "OPERATOR"


def test_password_hash_and_verify():
    encoded = hash_password("StrongPass123")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("StrongPass123", encoded)
    assert not verify_password("wrong", encoded)


def test_register_user_creates_viewer(monkeypatch):
    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDb:
        def query(self, _model):
            return FakeQuery()

        def add(self, _item):
            return None

        def commit(self):
            return None

        def refresh(self, item):
            if not item.user_id:
                item.user_id = uuid.uuid4()
            if not item.created_at:
                item.created_at = datetime.now(timezone.utc).replace(tzinfo=None)

    monkeypatch.setattr(auth_routes.cfg, "AUTH_USERS", {})
    result = auth_routes.register_user(
        auth_routes.RegisterRequest(username="newuser", password="StrongPass123"),
        db=FakeDb(),
    )
    assert result["username"] == "newuser"
    assert result["role"] == "VIEWER"
    assert result["is_active"] is True
