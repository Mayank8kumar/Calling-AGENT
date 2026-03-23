"""
# Security utility tests (7 tests)
# TestPasswordHashing:
#   - hash and verify works
#   - wrong password fails
#   - different hashes for same password (random salt)
# TestJWT:
#   - create and decode access token (sub, tenant_id, role)
#   - create and decode refresh token
#   - invalid token raises AuthenticationError
#   - extra claims preserved
"""

"""Tests for security utilities — JWT, passwords, encryption."""

import os
import pytest

# Set test env vars before importing
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-validation")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-long")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw==")  # base64

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.exceptions import AuthenticationError


class TestPasswordHashing:
    def test_hash_and_verify(self):
        plain = "mySecurePassword123"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct-password")
        assert not verify_password("wrong-password", hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2  # bcrypt uses random salt


class TestJWT:
    def test_create_and_decode_access_token(self):
        token = create_access_token(
            subject="user-123",
            tenant_id="tenant-456",
            role="admin",
        )
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["tenant_id"] == "tenant-456"
        assert payload["role"] == "admin"
        assert payload["type"] == "access"

    def test_create_and_decode_refresh_token(self):
        token = create_refresh_token(
            subject="user-123",
            tenant_id="tenant-456",
        )
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["type"] == "refresh"

    def test_invalid_token_raises(self):
        with pytest.raises(AuthenticationError):
            decode_token("invalid.token.here")

    def test_extra_claims(self):
        token = create_access_token(
            subject="user-123",
            tenant_id="tenant-456",
            extra={"custom_field": "custom_value"},
        )
        payload = decode_token(token)
        assert payload["custom_field"] == "custom_value"