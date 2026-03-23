"""
# Tenant management routes (all require auth)
# GET   /tenants/me — current tenant details (plan, limits, features)
# PATCH /tenants/me — update tenant (name, email, branding, features)
# GET   /tenants/me/usage — current month usage + plan limits + remaining minutes
# PATCH /tenants/me/providers — update provider config (bring-your-own API keys)
"""

"""
Tenant management API routes (admin-only).

Endpoints:
- GET    /tenants/me          — Get current tenant details
- PATCH  /tenants/me          — Update current tenant
- GET    /tenants/me/usage    — Get current usage stats
- PATCH  /tenants/me/providers — Update provider configuration
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.tenant_repo import TenantRepository
from app.db.session import get_db, get_redis
from app.middleware.auth import get_current_user, get_tenant_id
from app.services.billing import BillingService

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/me")
async def get_current_tenant(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    repo = TenantRepository(db)
    tenant = await repo.get_by_id(uuid.UUID(tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "email": tenant.email,
        "plan": tenant.plan,
        "status": tenant.status,
        "max_concurrent_calls": tenant.max_concurrent_calls,
        "max_monthly_minutes": tenant.max_monthly_minutes,
        "features": tenant.features,
        "branding": tenant.branding,
        "created_at": tenant.created_at.isoformat(),
    }


@router.patch("/me")
async def update_current_tenant(
    updates: dict[str, Any],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    allowed_fields = {"name", "email", "phone", "branding", "features"}
    filtered = {k: v for k, v in updates.items() if k in allowed_fields}
    if not filtered:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    repo = TenantRepository(db)
    tenant = await repo.update(uuid.UUID(tenant_id), **filtered)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"status": "updated"}


@router.get("/me/usage")
async def get_usage(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get current month's usage and plan limits."""
    redis = get_redis()
    billing = BillingService(redis)

    repo = TenantRepository(db)
    tenant = await repo.get_by_id(uuid.UUID(tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    usage = await billing.get_current_usage(tenant_id)
    limits = billing.get_plan_limits(tenant.plan)

    return {
        "plan": tenant.plan,
        "usage": usage,
        "limits": limits,
        "minutes_remaining": max(0, limits["max_monthly_minutes"] - usage["total_minutes"]),
    }


@router.patch("/me/providers")
async def update_provider_config(
    config: dict[str, Any],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Update tenant's provider configuration (bring-your-own keys)."""
    repo = TenantRepository(db)
    tenant = await repo.update_provider_config(uuid.UUID(tenant_id), config)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"status": "provider_config_updated"}