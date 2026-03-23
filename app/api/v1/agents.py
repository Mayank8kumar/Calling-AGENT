"""
# Agent CRUD routes (all require auth)
# POST   /agents — create new AI voice agent with prompt, voice, tools config
# GET    /agents — list all agents for current tenant
# GET    /agents/{id} — get agent details
# PATCH  /agents/{id} — update agent configuration
# DELETE /agents/{id} — soft-delete agent
"""
"""
Agent management API routes.

Endpoints:
- POST   /agents          — Create a new AI agent
- GET    /agents          — List agents for tenant
- GET    /agents/{id}     — Get agent details
- PATCH  /agents/{id}     — Update agent config
- DELETE /agents/{id}     — Soft-delete agent
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.auth import get_current_user, get_tenant_id
from app.models.agent import Agent
from app.schemas import AgentCreate, AgentResponse, AgentUpdate

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Agent:
    """Create a new AI voice agent."""
    agent = Agent(
        tenant_id=uuid.UUID(tenant_id),
        name=body.name,
        agent_type=body.agent_type,
        system_prompt=body.system_prompt,
        greeting_message=body.greeting_message,
        language=body.language,
        voice_id=body.voice_id,
        stt_provider=body.stt_provider,
        llm_provider=body.llm_provider,
        tts_provider=body.tts_provider,
        llm_model=body.llm_model,
        llm_temperature=body.llm_temperature,
        llm_max_tokens=body.llm_max_tokens,
        max_silence_seconds=body.max_silence_seconds,
        max_call_duration_seconds=body.max_call_duration_seconds,
        enable_recording=body.enable_recording,
        tools=body.tools,
        escalation_config=body.escalation_config,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[Agent]:
    """List all agents for the current tenant."""
    result = await db.execute(
        select(Agent)
        .where(Agent.tenant_id == uuid.UUID(tenant_id))
        .where(Agent.deleted_at.is_(None))
        .order_by(Agent.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Agent:
    """Get agent details."""
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .where(Agent.tenant_id == uuid.UUID(tenant_id))
        .where(Agent.deleted_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Agent:
    """Update agent configuration."""
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .where(Agent.tenant_id == uuid.UUID(tenant_id))
        .where(Agent.deleted_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = body.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(agent, field_name, value)

    await db.flush()
    await db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Soft-delete an agent."""
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .where(Agent.tenant_id == uuid.UUID(tenant_id))
        .where(Agent.deleted_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.soft_delete()