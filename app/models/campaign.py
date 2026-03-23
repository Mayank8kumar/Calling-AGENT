"""
# Campaign model — outbound calling campaigns
# Fields: name, agent_id FK, status (draft/scheduled/running/paused/completed/canceled)
# Schedule: scheduled_start, scheduled_end, timezone, calling_hours_start/end
# Contacts: contact_list JSONB [{phone, name, metadata}], total/contacted/answered counts
# Pacing: max_concurrent, retry_attempts, retry_delay_minutes
# Compliance: dnc_checked flag
"""

"""
Campaign model — outbound calling campaign definitions.
"""

from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantBase


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELED = "canceled"


class Campaign(TenantBase):
    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), default=CampaignStatus.DRAFT, nullable=False, index=True
    )

    # Scheduling
    scheduled_start: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_end: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    calling_hours_start: Mapped[str] = mapped_column(
        String(5), default="09:00", nullable=False, comment="HH:MM format"
    )
    calling_hours_end: Mapped[str] = mapped_column(
        String(5), default="21:00", nullable=False
    )

    # Contacts
    contact_list: Mapped[dict] = mapped_column(
        JSONB, default=list, nullable=False, server_default="[]",
        comment="Contact entries: [{phone, name, metadata}]",
    )
    total_contacts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contacted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Pacing
    max_concurrent: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    retry_delay_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)

    # DNC compliance
    dnc_checked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Notes
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Campaign {self.name} status={self.status}>"