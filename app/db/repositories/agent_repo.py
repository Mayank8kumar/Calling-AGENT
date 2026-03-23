"""
# Agent data access layer
# Methods:
#   get_by_id(agent_id, tenant_id)
#   get_default_inbound(tenant_id) — first active inbound agent (auto-routing)
#   list_by_tenant(tenant_id, agent_type, active_only)
#   create(**kwargs)
#   update(agent_id, tenant_id, **kwargs)
#   soft_delete(agent_id, tenant_id)
"""

"""
Agent repository — data access layer for agent CRUD and config resolution.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent


class AgentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(
        self, agent_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Agent | None:
        result = await self._db.execute(
            select(Agent)
            .where(Agent.id == agent_id)
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_default_inbound(self, tenant_id: uuid.UUID) -> Agent | None:
        """Get the first active inbound agent for a tenant (used for inbound routing)."""
        result = await self._db.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.agent_type.in_(["inbound", "hybrid"]))
            .where(Agent.is_active.is_(True))
            .where(Agent.deleted_at.is_(None))
            .order_by(Agent.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        tenant_id: uuid.UUID,
        agent_type: str | None = None,
        active_only: bool = True,
    ) -> list[Agent]:
        query = (
            select(Agent)
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.deleted_at.is_(None))
        )
        if active_only:
            query = query.where(Agent.is_active.is_(True))
        if agent_type:
            query = query.where(Agent.agent_type == agent_type)

        result = await self._db.execute(query.order_by(Agent.created_at.desc()))
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> Agent:
        agent = Agent(**kwargs)
        self._db.add(agent)
        await self._db.flush()
        await self._db.refresh(agent)
        return agent

    async def update(self, agent_id: uuid.UUID, tenant_id: uuid.UUID, **kwargs: Any) -> Agent | None:
        agent = await self.get_by_id(agent_id, tenant_id)
        if not agent:
            return None
        for key, value in kwargs.items():
            if hasattr(agent, key) and value is not None:
                setattr(agent, key, value)
        await self._db.flush()
        await self._db.refresh(agent)
        return agent

    async def soft_delete(self, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        agent = await self.get_by_id(agent_id, tenant_id)
        if not agent:
            return False
        agent.soft_delete()
        await self._db.flush()
        return True