"""
# Campaign management routes (all require auth)
# POST   /campaigns — create outbound campaign with contacts and schedule
# GET    /campaigns — list campaigns for tenant
# GET    /campaigns/{id} — campaign details with contact stats
# PATCH  /campaigns/{id} — update campaign config
# POST   /campaigns/{id}/start — start dialing (queues contacts via Celery)
# POST   /campaigns/{id}/pause — pause campaign
# POST   /campaigns/{id}/cancel — cancel campaign
"""
"""
Campaign management API routes.

Endpoints:
- POST   /campaigns              — Create campaign
- GET    /campaigns              — List campaigns
- GET    /campaigns/{id}         — Get campaign details
- PATCH  /campaigns/{id}         — Update campaign
- POST   /campaigns/{id}/start   — Start campaign execution
- POST   /campaigns/{id}/pause   — Pause campaign
- POST   /campaigns/{id}/cancel  — Cancel campaign
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.auth import get_current_user, get_tenant_id
from app.models.campaign import Campaign, CampaignStatus

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    agent_id: uuid.UUID
    timezone: str = "UTC"
    calling_hours_start: str = "09:00"
    calling_hours_end: str = "21:00"
    max_concurrent: int = Field(default=3, ge=1, le=50)
    retry_attempts: int = Field(default=2, ge=0, le=5)
    retry_delay_minutes: int = Field(default=60, ge=5)
    contact_list: list[dict[str, Any]] = []
    description: str | None = None


class CampaignUpdate(BaseModel):
    name: str | None = None
    calling_hours_start: str | None = None
    calling_hours_end: str | None = None
    max_concurrent: int | None = None
    contact_list: list[dict[str, Any]] | None = None
    description: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreate,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    campaign = Campaign(
        tenant_id=uuid.UUID(tenant_id),
        name=body.name,
        agent_id=body.agent_id,
        timezone=body.timezone,
        calling_hours_start=body.calling_hours_start,
        calling_hours_end=body.calling_hours_end,
        max_concurrent=body.max_concurrent,
        retry_attempts=body.retry_attempts,
        retry_delay_minutes=body.retry_delay_minutes,
        contact_list=body.contact_list,
        total_contacts=len(body.contact_list),
        description=body.description,
    )
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    return {"id": str(campaign.id), "name": campaign.name, "status": campaign.status}


@router.get("")
async def list_campaigns(
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.tenant_id == uuid.UUID(tenant_id))
        .where(Campaign.deleted_at.is_(None))
        .order_by(Campaign.created_at.desc())
    )
    campaigns = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "total_contacts": c.total_contacts,
            "contacted_count": c.contacted_count,
            "answered_count": c.answered_count,
            "created_at": c.created_at.isoformat(),
        }
        for c in campaigns
    ]


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .where(Campaign.tenant_id == uuid.UUID(tenant_id))
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "status": campaign.status,
        "agent_id": str(campaign.agent_id),
        "timezone": campaign.timezone,
        "calling_hours_start": campaign.calling_hours_start,
        "calling_hours_end": campaign.calling_hours_end,
        "max_concurrent": campaign.max_concurrent,
        "total_contacts": campaign.total_contacts,
        "contacted_count": campaign.contacted_count,
        "answered_count": campaign.answered_count,
        "contact_list": campaign.contact_list,
        "description": campaign.description,
        "created_at": campaign.created_at.isoformat(),
    }


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .where(Campaign.tenant_id == uuid.UUID(tenant_id))
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.PAUSED):
        raise HTTPException(status_code=400, detail=f"Cannot start campaign in status: {campaign.status}")

    if not campaign.contact_list:
        raise HTTPException(status_code=400, detail="Campaign has no contacts")

    campaign.status = CampaignStatus.RUNNING
    await db.flush()

    # Queue the campaign execution via Celery
    from app.tasks.call_tasks import schedule_outbound_call
    for contact in campaign.contact_list:
        if contact.get("status") not in ("completed", "in_progress"):
            schedule_outbound_call.delay(
                campaign_id=str(campaign_id),
                contact_phone=contact["phone"],
                contact_name=contact.get("name", ""),
                tenant_id=tenant_id,
                agent_id=str(campaign.agent_id),
            )

    return {"status": "running", "queued_contacts": len(campaign.contact_list)}


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .where(Campaign.tenant_id == uuid.UUID(tenant_id))
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = CampaignStatus.PAUSED
    return {"status": "paused"}


@router.post("/{campaign_id}/cancel")
async def cancel_campaign(
    campaign_id: uuid.UUID,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .where(Campaign.tenant_id == uuid.UUID(tenant_id))
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = CampaignStatus.CANCELED
    return {"status": "canceled"}