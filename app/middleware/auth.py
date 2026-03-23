"""
# Authentication & authorization middleware
# Dependencies (used in route functions):
#   get_current_user — extracts JWT from Authorization header, validates, sets tenant RLS
#   get_tenant_id — extracts tenant_id from authenticated user's JWT
#   require_role(*roles) — factory that checks user's role against allowed roles
#   optional_auth — returns user or None (for public endpoints)
# How it works:
#   1. HTTPBearer extracts token from "Authorization: Bearer <token>"
#   2. decode_token() validates JWT signature and expiry
#   3. tenant_id from JWT is set in current_tenant_id ContextVar
#   4. get_db() picks up the ContextVar and sets PostgreSQL RLS context
"""

# File: voice-agent-platform/app/middleware/auth.py
"""
Middleware and FastAPI dependencies for:
- JWT authentication
- Tenant extraction and RLS context propagation
- Rate limiting
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_token
from app.db.session import current_tenant_id

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> dict[str, Any]:
    """
    Extract and validate JWT token from Authorization header.
    Returns the decoded token payload with sub, tenant_id, role.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials)
    except AuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # Set tenant context for RLS
    tenant_id = payload.get("tenant_id")
    if tenant_id:
        current_tenant_id.set(tenant_id)

    return payload


async def require_role(*allowed_roles: str):
    """Factory for role-based access control dependencies."""

    async def _check_role(
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> dict[str, Any]:
        role = user.get("role", "")
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' not permitted. Required: {allowed_roles}",
            )
        return user

    return _check_role


# Convenience dependencies
RequireAdmin = Depends(require_role("owner", "admin"))
RequireOwner = Depends(require_role("owner"))


async def get_tenant_id(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> str:
    """Extract tenant_id from the authenticated user's JWT."""
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No tenant_id in token",
        )
    return tenant_id


async def optional_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> dict[str, Any] | None:
    """Optional authentication — returns None if no token present."""
    if not credentials:
        return None
    try:
        return decode_token(credentials.credentials)
    except AuthenticationError:
        return None