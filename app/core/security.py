"""
# Security utilities
# Passwords: hash_password(), verify_password() — bcrypt
# JWT: create_access_token(), create_refresh_token(), decode_token()
#   - Access tokens: short-lived (30 min), contain sub + tenant_id + role
#   - Refresh tokens: long-lived (7 days), used to get new access tokens
# PII Encryption: encrypt_pii(), decrypt_pii() — Fernet (AES-128-CBC)
# Webhook verify: verify_twilio_signature(), verify_hmac_signature()
"""

"""
Security utilities: JWT token management, password hashing, PII encryption.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings
from app.core.exceptions import AuthenticationError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_fernet_instance: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazy-init Fernet cipher from config. Key must be 32-byte URL-safe base64."""
    global _fernet_instance
    if _fernet_instance is None:
        key = get_settings().encryption_key
        if not key:
            raise ValueError("ENCRYPTION_KEY is required for PII encryption")
        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet_instance


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

def create_access_token(
    subject: str,
    tenant_id: str,
    role: str = "user",
    extra: dict[str, Any] | None = None,
) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(UTC),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, tenant_id: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "exp": expire,
        "iat": datetime.now(UTC),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises AuthenticationError on failure."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if "sub" not in payload or "tenant_id" not in payload:
            raise AuthenticationError("Invalid token payload")
        return payload
    except JWTError as exc:
        raise AuthenticationError(f"Token validation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# PII encryption (Fernet symmetric — AES-128-CBC)
# ---------------------------------------------------------------------------

def encrypt_pii(plaintext: str) -> str:
    """Encrypt a PII string. Returns base64-encoded ciphertext."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_pii(ciphertext: str) -> str:
    """Decrypt a PII string."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def verify_twilio_signature(url: str, params: dict[str, str], signature: str) -> bool:
    """Verify Twilio webhook signature (X-Twilio-Signature header)."""
    from twilio.request_validator import RequestValidator

    settings = get_settings()
    if not settings.twilio_auth_token:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, params, signature)


def verify_hmac_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Generic HMAC-SHA256 webhook signature verification."""
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)