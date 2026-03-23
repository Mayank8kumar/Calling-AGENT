"""
# Authentication routes
# POST /auth/register — creates tenant + admin user, returns JWT tokens
# POST /auth/login — validates email/password, returns access + refresh tokens
# POST /auth/refresh — exchanges refresh token for new access token
# GET  /auth/me — returns current user info (requires auth)
"""
# File: voice-agent-platform/app/api/v1/auth.py
"""
Authentication API routes.

Endpoints:
- POST /auth/register   — Register new tenant + admin user
- POST /auth/login      — Login and get JWT tokens
- POST /auth/refresh    — Refresh access token
- GET  /auth/me         — Get current user info
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.middleware.auth import get_current_user
from app.models.tenant import Tenant, TenantPlan, TenantStatus
from app.models.user import User, UserRole
from app.schemas import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(LoginRequest):
    full_name: str
    tenant_name: str
    tenant_slug: str


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Register a new tenant organization and admin user."""
    # Check for existing tenant slug
    result = await db.execute(select(Tenant).where(Tenant.slug == body.tenant_slug))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant slug '{body.tenant_slug}' already exists",
        )

    # Check for existing email
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create tenant
    tenant = Tenant(
        name=body.tenant_name,
        slug=body.tenant_slug,
        email=body.email,
        plan=TenantPlan.FREE,
        status=TenantStatus.TRIAL,
    )
    db.add(tenant)
    await db.flush()

    # Create admin user
    user = User(
        tenant_id=tenant.id,
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=UserRole.OWNER,
    )
    db.add(user)
    await db.flush()

    # Generate tokens
    access_token = create_access_token(
        subject=str(user.id),
        tenant_id=str(tenant.id),
        role=user.role,
    )
    refresh_token = create_refresh_token(
        subject=str(user.id),
        tenant_id=str(tenant.id),
    )

    from app.config import get_settings
    settings = get_settings()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Authenticate user and return JWT tokens."""
    result = await db.execute(
        select(User).where(User.email == body.email).where(User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    access_token = create_access_token(
        subject=str(user.id),
        tenant_id=str(user.tenant_id),
        role=user.role,
    )
    refresh_token = create_refresh_token(
        subject=str(user.id),
        tenant_id=str(user.tenant_id),
    )

    from app.config import get_settings
    settings = get_settings()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Refresh an expired access token using a valid refresh token."""
    payload = decode_token(body.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload["sub"]
    tenant_id = payload["tenant_id"]

    # Verify user still exists and is active
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(user_id)).where(User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User no longer active")

    access_token = create_access_token(
        subject=user_id,
        tenant_id=tenant_id,
        role=user.role,
    )
    new_refresh = create_refresh_token(subject=user_id, tenant_id=tenant_id)

    from app.config import get_settings
    settings = get_settings()

    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


@router.get("/me")
async def get_me(
    user: Annotated[dict[str, Any], Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get current authenticated user info."""
    result = await db.execute(select(User).where(User.id == uuid.UUID(user["sub"])))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": str(db_user.id),
        "email": db_user.email,
        "full_name": db_user.full_name,
        "role": db_user.role,
        "tenant_id": str(db_user.tenant_id),
    }