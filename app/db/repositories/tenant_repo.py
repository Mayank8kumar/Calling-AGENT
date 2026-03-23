"""
# Tenant data access layer
# Methods:
#   get_by_id(tenant_id) — lookup by UUID
#   get_by_slug(slug) — lookup by URL slug
#   get_by_phone(phone) — lookup by phone number (for inbound call routing)
#   list_active() — paginated list of active tenants
#   create(**kwargs) — create new tenant
#   update(tenant_id, **kwargs) — update fields
#   update_provider_config(tenant_id, config) — merge provider overrides
#   suspend(tenant_id) — deactivate tenant
#   get_concurrent_limit(tenant_id) — get max concurrent calls
"""

"""
Tenant repository — data access layer for tenant CRUD and config.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantStatus


class TenantRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        result = await self._db.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self._db.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    async def get_by_phone(self, phone_number: str) -> Tenant | None:
        """Look up tenant by assigned phone number (for inbound call routing)."""
        # Search in provider_config JSONB for phone number mappings
        result = await self._db.execute(
            select(Tenant).where(
                Tenant.provider_config["phone_numbers"].astext.contains(phone_number)
            )
        )
        return result.scalar_one_or_none()

    async def list_active(self, offset: int = 0, limit: int = 50) -> list[Tenant]:
        result = await self._db.execute(
            select(Tenant)
            .where(Tenant.is_active.is_(True))
            .order_by(Tenant.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> Tenant:
        tenant = Tenant(**kwargs)
        self._db.add(tenant)
        await self._db.flush()
        await self._db.refresh(tenant)
        return tenant

    async def update(self, tenant_id: uuid.UUID, **kwargs: Any) -> Tenant | None:
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            return None
        for key, value in kwargs.items():
            if hasattr(tenant, key):
                setattr(tenant, key, value)
        await self._db.flush()
        await self._db.refresh(tenant)
        return tenant

    async def update_provider_config(
        self, tenant_id: uuid.UUID, provider_config: dict[str, Any]
    ) -> Tenant | None:
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            return None
        # Merge new config with existing
        existing = tenant.provider_config or {}
        existing.update(provider_config)
        tenant.provider_config = existing
        await self._db.flush()
        await self._db.refresh(tenant)
        return tenant

    async def suspend(self, tenant_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(status=TenantStatus.SUSPENDED, is_active=False)
        )
        return result.rowcount > 0

    async def get_concurrent_limit(self, tenant_id: uuid.UUID) -> int:
        tenant = await self.get_by_id(tenant_id)
        return tenant.max_concurrent_calls if tenant else 5 